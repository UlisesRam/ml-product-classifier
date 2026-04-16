# Pipeline de Gestión Semanal de Términos Orgánicos — Buscador E-commerce

Automatización end-to-end del proceso de actualización semanal de listados de productos
en el buscador de un e-commerce de gran escala (~390 reglas activas en producción).

## Contexto del problema

El buscador de un e-commerce de alto tráfico muestra productos distintos según el término
que el usuario escribe. Determinar qué productos mostrar (y en qué orden) para cada término
es un proceso que originalmente requería decisiones manuales artículo por artículo.

Este pipeline automatiza ese proceso combinando:
- **Datos cuantitativos** (comportamiento del usuario: vistas, carritos, conversión)
- **Criterio del equipo analítico** (apuestas estratégicas por término)
- **Clasificación automática** (modelo Random Forest para artículos sin historial)

## Arquitectura del pipeline

```
[Diccionario de términos]  [Buscador + Ventas]  [Apuestas de especialistas]
         │                        │                        │
         ▼                        ▼                        │
  1. Sincronización          2. Score de              3. Cruce de
     del dashboard              performance              listados
     (PreProcesos)              (CalculosPerformance)    (GeneradorListados)
                                        │                        │
                                        ▼                        │
                               Clasificación Si/No               │
                               (Manual o Modelo RF)              │
                                        └────────────────────────┘
                                                     │
                                                     ▼
                                        4. Actualización en el
                                           motor de búsqueda
                                           (ApisConnections)
```

## Módulos

| Archivo | Clase | Responsabilidad |
|---|---|---|
| `preprocesos.py` | `PreProcesos` | Sincronización de Google Sheets y gestión del histórico de decisiones |
| `calculosperformance.py` | `CalculosPerformance` | Cálculo del score de performance y clasificación de artículos |
| `listados.py` | `GeneradorListados` | Generación y mezcla de listados finales por término |
| `conexionesapis.py` | `ApisConnections` | Conexión REST con el motor de búsqueda (Lucidworks Fusion) |

## Descripción técnica

### Score de performance

Para cada par (término de búsqueda, SKU) se calcula un score ponderado de métricas
de comportamiento del usuario:

```
score = w_vistas × vistas_norm
      + w_carrito × add2cart_norm
      + w_ingreso × ingreso_norm
      + w_unidades × unidades_norm
      + w_tasa × est_vta_norm
```

La normalización es **logarítmica min-max por grupo** (por término), lo que:
- Reduce el impacto de outliers en términos con mucho volumen
- Hace el ranking relativo al contexto de búsqueda, no global

### Mezcla de listados

El listado final para cada término se construye en 5 pasos:

1. **Extracción de fijos**: SKUs prioritarios que van siempre al inicio
2. **Mezcla intercalada**: `take_a` SKUs de performance + `take_b` de especialistas (configurable por término)
3. **Separación 1P/3P**: Dividir entre productos propios y marketplace
4. **Rebalanceo**: Ajustar proporción 1P/3P según parámetros del diccionario
5. **Ensamblado final**: Fijos + mezcla rebalanceada

### Clasificación automática de artículos

Los SKUs sin historial de decisión se clasifican con un modelo **Random Forest**:
- Input: término representativo + descripción del artículo (preprocesado con stemming en español + TF-IDF)
- Output: Si (el producto es relevante para ese término) / No
- Accuracy y recall: **0.94** sobre el conjunto de validación

## Stack tecnológico

- **Python** — pandas, numpy, scikit-learn, joblib, nltk, requests
- **Google Sheets API** — gspread (sincronización de datos)
- **Lucidworks Fusion API** — actualización de reglas del motor de búsqueda
- **Google Colab** — entorno de ejecución con autenticación OAuth

## Configuración

Todas las variables de configuración están centralizadas en la primera celda del notebook:

```python
SPREADSHEET_DICCIONARIO = '[NOMBRE_SHEET_DICCIONARIO]'
HISTORICO_DE_DECISIONES = '/ruta/al/compilado_decisiones.csv'
RUTA_FOLDER_TRABAJO = '/ruta/a/la/carpeta/de/trabajo/'
PATH_DEL_MODELO = '/ruta/al/modelo_clasificador.pkl'
TAG_LW = '[TAG_REGLAS]'
```

## Notas de seguridad

- Las credenciales del motor de búsqueda se ingresan **de forma interactiva** en tiempo de ejecución, nunca se almacenan en el código
- La autenticación con Google se realiza mediante OAuth 2.0 estándar de Google Colab
- Los nombres de hojas de cálculo y rutas internas han sido anonimizados en este repositorio
