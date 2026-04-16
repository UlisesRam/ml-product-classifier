# -*- coding: utf-8 -*-
"""
calculosperformance.py
======================
Módulo de cálculo de performance para términos de búsqueda en un e-commerce.

Propósito:
    Calcular un score de relevancia para cada par (término de búsqueda, SKU)
    combinando métricas de comportamiento del usuario (vistas, carritos, ingresos)
    con datos de inventario y decisiones previas del equipo analítico.

El score resultante determina el orden en que los productos aparecen
en el buscador para cada término, priorizando los artículos que han
demostrado mayor conversión e intención de compra.

Pipeline principal (calcular_performance):
    1. Normalización de columnas y cruces entre fuentes de datos.
    2. Cálculo de tasa de venta sobre carrito.
    3. Agregación de métricas por término-SKU.
    4. Normalización logarítmica min-max por grupo.
    5. Score ponderado final y ordenamiento.
    6. Cruce con decisiones históricas del equipo.

Clasificación de SKUs pendientes (gestionar_clasificacion):
    Los SKUs sin decisión histórica se etiquetan como 'Revisar' y
    pueden clasificarse de forma manual (exportando a Excel) o
    automática (usando el modelo Random Forest entrenado).
"""

import pandas as pd
import numpy as np
import os
import re
import joblib
from nltk.stem import SnowballStemmer
from datetime import datetime


class CalculosPerformance:
    """
    Calcula scores de performance por término-SKU y gestiona la clasificación
    de artículos nuevos o sin historial de decisión.

    Attributes:
        model_rf: Modelo Random Forest para clasificación automática de SKUs.
                  None si no se proporciona ruta o el archivo no existe.
        tfidf: Vectorizador TF-IDF para transformar texto antes de la predicción.
               None si no se proporciona ruta o el archivo no existe.
        stemmer: SnowballStemmer en español para preprocesar texto de artículos.
        carpeta_salida (str): Carpeta donde se guardan los Excels generados.
    """

    def __init__(self, path_modelo=None, path_tfidf=None, carpeta_salida="Resultados"):
        """
        Inicializa la clase cargando el modelo y vectorizador si existen.

        Args:
            path_modelo (str, optional): Ruta al archivo .pkl del modelo Random Forest.
            path_tfidf (str, optional): Ruta al archivo .pkl del vectorizador TF-IDF.
            carpeta_salida (str): Carpeta de salida para Excels generados.
                                  Se crea automáticamente si no existe.
        """
        self.model_rf = joblib.load(path_modelo) \
            if path_modelo and os.path.exists(path_modelo) else None
        self.tfidf = joblib.load(path_tfidf) \
            if path_tfidf and os.path.exists(path_tfidf) else None
        self.stemmer = SnowballStemmer('spanish')
        self.carpeta_salida = carpeta_salida

        if not os.path.exists(self.carpeta_salida):
            os.makedirs(self.carpeta_salida)

    def _limpieza_con_stemming(self, texto):
        """
        Preprocesa texto para el modelo: minúsculas, eliminación de
        caracteres especiales y reducción de palabras a su raíz (stemming).

        Ejemplo: 'Televisores LED 4K' → 'televis led 4k'

        Args:
            texto (str): Texto a preprocesar (nombre de artículo o término).

        Returns:
            str: Texto limpio y stemmizado, listo para vectorización TF-IDF.
        """
        if not isinstance(texto, str):
            return ""
        texto = texto.lower()
        texto = re.sub(r'[^a-z0-9\sáéíóúüñ]', ' ', texto).strip()
        palabras = [self.stemmer.stem(w) for w in texto.split()]
        return " ".join(palabras)

    def calcular_performance(self, df_buscador, df_domo, df_dict, df_decisiones,
                              mkp, onep,
                              w_vistas=0.48, w_carrito=0.48, w_ingreso=0.02,
                              w_unidades=0.02, w_tasa=0.02,
                              modo_cruce='termino',
                              dict_renombres=None,
                              cols_agrupacion=None,
                              cols_ordenamiento=None,
                              orden_ascendente=None):
        """
        Calcula el score de performance para cada par (término, SKU) combinando
        métricas de comportamiento del usuario con datos de catálogo e inventario.

        El score es una suma ponderada de cinco métricas normalizadas:
            score = w_vistas*vistas_norm + w_carrito*add2cart_norm
                  + w_ingreso*ingreso_norm + w_unidades*unidades_norm
                  + w_tasa*est_vta_norm

        La normalización es logarítmica min-max por grupo (por término), lo que
        evita que artículos con volumen extremo dominen el ranking y permite
        comparar artículos dentro del mismo contexto de búsqueda.

        Args:
            df_buscador (pd.DataFrame): Datos del buscador (términos buscados y
                SKUs mostrados con sus métricas de vistas y carritos).
            df_domo (pd.DataFrame): Catálogo maestro de artículos con atributos
                (descripción, categoría, marca, precio, inventario).
            df_dict (pd.DataFrame): Diccionario de términos con mapeo
                término → término representativo → ID de regla.
            df_decisiones (pd.DataFrame): Historial de decisiones del equipo
                analítico (Si/No) por par SKU-término.
            mkp (pd.DataFrame): Datos de visitas y conversión del canal marketplace.
            onep (pd.DataFrame): Datos de visitas y conversión del canal propio
                (1P: primera parte).
            w_vistas (float): Peso de las vistas en el score final.
            w_carrito (float): Peso de los agregados a carrito.
            w_ingreso (float): Peso del ingreso generado.
            w_unidades (float): Peso de las unidades vendidas.
            w_tasa (float): Peso de la tasa de venta sobre carrito.
            modo_cruce (str): Estrategia de cruce con el diccionario.
                'termino' - cruza por término de búsqueda (flujo estándar).
                'sku_rank' - cruza por SKU con ranking de especialistas.
            dict_renombres (dict, optional): Mapeo de nombres de columnas para
                estandarizar el diccionario de entrada.
            cols_agrupacion (list, optional): Columnas a conservar en la
                agregación de métricas.
            cols_ordenamiento (list, optional): Columnas para el ordenamiento final.
            orden_ascendente (list, optional): Dirección del ordenamiento por columna.

        Returns:
            pd.DataFrame: DataFrame con el score de performance por SKU-término,
                sequence final y la decisión del equipo (Si/No/Revisar).
        """
        # --- CONFIGURACIÓN POR DEFECTO ---
        # Mapeo de variantes de nombres para la columna de término representativo
        if dict_renombres is None:
            dict_renombres = {
                'término': 'término',
                'termino': 'término',
                'termino representativo': 'termino_agg',
                'término representativo': 'termino_agg',
                'representativo': 'termino_agg',
                'termino_agrupado': 'termino_agg',
                'Termino Representativo': 'termino_agg',
                'Term Representativo': 'termino_agg',
                'term representativo': 'termino_agg'
            }

        if cols_agrupacion is None:
            cols_base = [
                'analista', 'termino_agg', 'id', 'tipo_carga', 'sku',
                'des_articulo', 'des_area', 'des_categoria', 'des_marca',
                'imp_precioventa', 'imp_preciodescuento', 'por_descuento',
                'por_cobertura', 'num_existenciatopoe', 'num_existencia'
            ]
            if modo_cruce == 'sku_rank':
                cols_base.append('rank')
            cols_agrupacion = cols_base

        if cols_ordenamiento is None:
            cols_ordenamiento = ['analista', 'termino_agg', 'score_preliminar']
            orden_ascendente = [True, True, False]

        # 1. Normalización de nombres de columnas (lowercase + strip en todos los DFs)
        for df in [df_buscador, df_domo, df_dict, df_decisiones, mkp, onep]:
            df.columns = df.columns.str.lower().str.strip()

        df_dict = df_dict.rename(columns=dict_renombres)

        # Limpiar strings en columnas clave para evitar mismatches por espacios/mayúsculas
        for col in ['termino_agg', 'término']:
            if col in df_dict.columns:
                df_dict[col] = df_dict[col].astype(str).str.lower().str.strip()

        if 'terminobusqueda' in df_buscador.columns:
            df_buscador['terminobusqueda'] = \
                df_buscador['terminobusqueda'].astype(str).str.lower().str.strip()

        if 'termino_agg' in df_decisiones.columns:
            df_decisiones['termino_agg'] = \
                df_decisiones['termino_agg'].astype(str).str.lower().str.strip()

        # 2. Procesamiento de ventas: unificar canales 1P y Marketplace
        onep_cp = onep.copy()
        mkp_cp = mkp.copy()

        # Generar SKU unificado para canal propio (1P) según área de negocio:
        # Área 3 → formato PM-XXXXXXX3 (productos muebles/hogar)
        # Área 2 → formato PR-XXXXXXX2 (productos ropa/calzado)
        cond_sku = [onep_cp['idu_areacodigo'] == 3, onep_cp['idu_areacodigo'] == 2]
        opts_sku = [
            'PM-' + onep_cp['idu_articulocodigo'].astype(str) + '3',
            'PR-' + onep_cp['idu_articulocodigo'].astype(str) + '2'
        ]
        onep_cp['nom_partnumber'] = np.select(cond_sku, opts_sku, default=np.nan)

        # Homologar nombres de columnas entre canales para poder concatenar
        onep_cp.rename(columns={
            'des_canalventa': 'des_canal',
            'imp_preciopromocion': 'imp_preciodescuento'
        }, inplace=True)
        mkp_cp.rename(columns={
            'imp_importefinalizado': 'imp_importefin',
            'imp_importefacturado': 'imp_importefac',
            'imp_preciooriginal': 'imp_precioventa',
            'des_area': 'des_arearmz'
        }, inplace=True)

        # Unificar ambos canales y calcular tasa de venta sobre carrito por SKU
        concatenado = pd.concat([onep_cp, mkp_cp], ignore_index=True)
        df_ventas_agrupado = concatenado.groupby('nom_partnumber', as_index=False).agg({
            'num_visitas': 'sum',
            'num_carritos': 'sum',
            'num_cantidadfin': 'sum'
        })
        df_ventas_agrupado['tasa_vta_carr'] = (
            df_ventas_agrupado['num_cantidadfin'] /
            df_ventas_agrupado['num_carritos']
        ).fillna(0)
        print(f'Tasa de vta/carrito calculada: '
              f'{df_ventas_agrupado["tasa_vta_carr"].count()} registros')

        # 3. Cruce maestro: buscador + diccionario + catálogo
        # Usar el SKU más representativo del catálogo (sin duplicados)
        df_domo_c = df_domo.sort_values(
            by=['des_identifier_1', 'des_identifier_2']
        ).drop_duplicates(subset=['nom_partnumber']).copy()
        df_domo_c = df_domo_c.rename(columns={'nom_partnumber': 'sku'})

        if modo_cruce == 'termino':
            # Flujo estándar: cruce por término buscado
            skus_perf = df_buscador.merge(
                df_dict, how='left', left_on='terminobusqueda', right_on='término'
            )
            skus_perf = skus_perf.merge(df_domo_c, how='left', on='sku')
        elif modo_cruce == 'sku_rank':
            # Flujo de especialistas: cruce por SKU con ranking de apuestas
            skus_perf = df_buscador.merge(df_dict, how='outer', on='sku')
            skus_perf = skus_perf.merge(df_domo_c, how='left', on='sku')
        else:
            raise ValueError("Modo de cruce no reconocido. Usa 'termino' o 'sku_rank'.")

        # 4. Agregación de métricas de performance por SKU dentro de cada término
        cols_validas_agrupar = [c for c in cols_agrupacion if c in skus_perf.columns]
        columna_agrupadora_principal = 'termino_agg'

        if 'termino_agg' not in cols_validas_agrupar:
            raise ValueError(
                "No se encontró la columna 'termino_agg' para agrupar. "
                "Verifica el dict_renombres."
            )

        calculo_performance = skus_perf.groupby(
            cols_validas_agrupar, dropna=False
        ).agg({
            'vistas': 'sum',
            'agregado a carrito np.': 'sum',
            'num_unidades': 'sum',
            'imp_ingreso': 'sum'
        }).reset_index()

        # Agregar tasa de venta por SKU al DataFrame de performance
        calculo_performance = calculo_performance.merge(
            df_ventas_agrupado[['nom_partnumber', 'tasa_vta_carr']],
            how='left', left_on='sku', right_on='nom_partnumber'
        ).fillna({'tasa_vta_carr': 0})

        # 5. Estimado de venta: precio efectivo × tasa venta × carritos
        # Se usa el precio con descuento si es válido (menor al normal y > $1)
        col_precio_desc = 'imp_preciodescuento' if 'imp_preciodescuento' \
                           in calculo_performance.columns else 'imp_precioventa'

        precio_valido = np.where(
            (calculo_performance.get(col_precio_desc, 0) <
             calculo_performance.get('imp_precioventa', 0)) &
            (calculo_performance.get(col_precio_desc, 0) > 1),
            calculo_performance.get(col_precio_desc, 0),
            calculo_performance.get('imp_precioventa', 0)
        )
        calculo_performance['est_vta'] = (
            precio_valido *
            calculo_performance['tasa_vta_carr'] *
            calculo_performance['agregado a carrito np.']
        )

        # 6. Normalización logarítmica min-max por término
        # Se aplica log(1+x) antes del min-max para reducir el impacto de outliers.
        # La normalización es POR GRUPO (por término) para que el ranking sea
        # relativo dentro del contexto de búsqueda, no global.
        columnas_norm = {
            'vistas': 'visitas_norm',
            'agregado a carrito np.': 'add2cart_norm',
            'imp_ingreso': 'ingreso_norm',
            'num_unidades': 'unidades_norm',
            'est_vta': 'est_vta_norm'
        }

        for col_orig, col_dest in columnas_norm.items():
            if col_orig in calculo_performance.columns:
                serie_log = np.log1p(calculo_performance[col_orig])

                if columna_agrupadora_principal in calculo_performance.columns:
                    grupo = serie_log.groupby(calculo_performance[columna_agrupadora_principal])
                    min_v = grupo.transform('min')
                    max_v = grupo.transform('max')
                    denominador = max_v - min_v
                    calculo_performance[col_dest] = np.where(
                        denominador == 0, 0, (serie_log - min_v) / denominador
                    )
                else:
                    # Fallback: normalización global si no hay columna agrupadora
                    min_v, max_v = serie_log.min(), serie_log.max()
                    denominador = max_v - min_v
                    calculo_performance[col_dest] = np.where(
                        denominador == 0, 0, (serie_log - min_v) / denominador
                    )

                calculo_performance[col_dest] = calculo_performance[col_dest].fillna(0)

        # 7. Score preliminar ponderado
        calculo_performance['score_preliminar'] = (
            w_vistas   * calculo_performance.get('visitas_norm', 0) +
            w_carrito  * calculo_performance.get('add2cart_norm', 0) +
            w_ingreso  * calculo_performance.get('ingreso_norm', 0) +
            w_unidades * calculo_performance.get('unidades_norm', 0) +
            w_tasa     * calculo_performance.get('est_vta_norm', 0)
        )

        # Ordenamiento y secuencia preliminar
        cols_sort_final = [c for c in cols_ordenamiento if c in calculo_performance.columns]
        asc_sort_final = [
            orden_ascendente[i]
            for i, c in enumerate(cols_ordenamiento)
            if c in calculo_performance.columns
        ]

        if modo_cruce == 'sku_rank' and 'rank' in calculo_performance.columns:
            calculo_performance = calculo_performance.sort_values(
                by=['termino_agg', 'rank'], ascending=[True, False], ignore_index=True
            )
        elif cols_sort_final:
            calculo_performance = calculo_performance.sort_values(
                by=cols_sort_final, ascending=asc_sort_final, ignore_index=True
            )

        calculo_performance['sequence_preliminar'] = calculo_performance.groupby(
            columna_agrupadora_principal
        ).cumcount() + 1

        # Re-ordenamiento y secuencia final
        if modo_cruce == 'termino' and columna_agrupadora_principal in calculo_performance.columns:
            cols_sort_inv = ['analista', columna_agrupadora_principal, 'sequence_preliminar']
            cols_validas_sort = [c for c in cols_sort_inv if c in calculo_performance.columns]
            calculo_performance.sort_values(
                by=cols_validas_sort, ascending=True, inplace=True, ignore_index=True
            )

        if columna_agrupadora_principal in calculo_performance.columns:
            calculo_performance['sequence_final'] = calculo_performance.groupby(
                columna_agrupadora_principal
            ).cumcount() + 1
        else:
            calculo_performance['sequence_final'] = 1

        # 8. Cruce con historial de decisiones del equipo analítico
        # Se construye una llave compuesta término+SKU para el cruce preciso
        if 'termino_agg' in calculo_performance.columns:
            calculo_performance['llave_final'] = (
                calculo_performance['termino_agg'].astype(str) + "-" +
                calculo_performance['sku'].astype(str)
            )
        else:
            calculo_performance['llave_final'] = calculo_performance['sku'].astype(str)

        if 'termino_agg' in df_decisiones.columns:
            df_decisiones['llave_final'] = (
                df_decisiones['termino_agg'].astype(str) + "-" +
                df_decisiones['sku'].astype(str)
            )

        df_final = pd.merge(
            calculo_performance,
            df_decisiones[['llave_final', 'decision_final']],
            on='llave_final', how='left'
        )
        # SKUs sin historial se marcan como 'Revisar' para clasificación posterior
        df_final["decision_final"] = df_final["decision_final"].fillna("Revisar")

        return df_final.drop(columns=['llave_final', 'nom_partnumber', 'prefix'],
                              errors='ignore')

    def exportar_excel(self, df, prefijo="Resultados_performance"):
        """
        Exporta un DataFrame a Excel con nombre fechado automáticamente.

        El nombre del archivo sigue el formato: {prefijo}_DD-MM-YYYY.xlsx
        Este formato es requerido por _get_latest_file() en preprocesos.py
        para la detección automática del archivo más reciente.

        Args:
            df (pd.DataFrame): DataFrame a exportar.
            prefijo (str): Prefijo del nombre del archivo.

        Returns:
            str: Ruta completa del archivo generado.
        """
        fecha_str = datetime.now().strftime("%d-%m-%Y")
        nombre_archivo = f"{prefijo}_{fecha_str}.xlsx"
        ruta_completa = os.path.join(self.carpeta_salida, nombre_archivo)
        df.to_excel(ruta_completa, index=False)
        print(f"Excel generado: {ruta_completa}")
        return ruta_completa

    def gestionar_clasificacion(self, df_resultado,
                                 cols_input=['termino_agg', 'des_articulo']):
        """
        Gestiona la clasificación de los registros marcados como 'Revisar',
        ofreciendo dos caminos: revisión manual o clasificación automática
        con el modelo Random Forest entrenado.

        Opción 1 - Revisión manual:
            Exporta el DataFrame a Excel para que el analista etiquete
            manualmente cada par SKU-término como Si/No.

        Opción 2 - Modelo automático:
            Separa los registros 'Revisar', aplica preprocesamiento de texto
            (limpieza + stemming + TF-IDF) y usa el modelo RF para predecir
            Si/No según similitud con el historial de decisiones.

        Args:
            df_resultado (pd.DataFrame): DataFrame de performance con la columna
                'decision_final' que puede tener valores 'Si', 'No' o 'Revisar'.
            cols_input (list): Columnas de texto a combinar como input del modelo.
                Por defecto: término representativo + descripción del artículo.

        Returns:
            pd.DataFrame | None: DataFrame con todas las decisiones resueltas,
                o None si el usuario eligió revisión manual (el proceso se pausa).
        """
        print(f"\nSe detectaron registros con etiqueta 'Revisar'.")
        print("1. REVISIÓN MANUAL")
        print("2. REVISIÓN MODELO")

        opcion = input("Selecciona 1 o 2: ")

        if opcion == "1":
            self.exportar_excel(df_resultado)
            print("Proceso pausado. Abre el Excel, edita la columna 'decision_final' "
                  "y guárdalo. Luego usa cargar_excel_revisado() para continuar.")
            return None

        elif opcion == "2":
            # Separar registros ya decididos de los pendientes
            df_listo = df_resultado[df_resultado['decision_final'] != 'Revisar'].copy()
            df_pendientes = df_resultado[df_resultado['decision_final'] == 'Revisar'].copy()

            print(f"Clasificando {len(df_pendientes)} registros con el modelo...")

            # Preprocesar texto combinando las columnas de input con stemming
            def preparar_fila(row):
                return " ".join([
                    self._limpieza_con_stemming(str(row[col]))
                    for col in cols_input if col in row.index
                ])

            df_pendientes['input_stemmed'] = df_pendientes.apply(preparar_fila, axis=1)

            # Vectorizar con TF-IDF y predecir con Random Forest
            X_tfidf = self.tfidf.transform(df_pendientes['input_stemmed'])
            preds = self.model_rf.predict(X_tfidf)
            df_pendientes['decision_final'] = np.where(preds == 1, 'Si', 'No')

            # Reunificar registros ya decididos con los recién clasificados
            df_final = pd.concat(
                [df_listo, df_pendientes.drop(columns=['input_stemmed'])],
                ignore_index=True
            )
            df_final = df_final.drop_duplicates()
            self.exportar_excel(df_final)
            return df_final

        return df_resultado

    def cargar_excel_revisado(self, nombre_archivo):
        """
        Carga un Excel revisado manualmente desde la carpeta de salida.

        Se usa después de que el analista editó el Excel exportado por
        gestionar_clasificacion(opcion=1) para continuar el pipeline.

        Args:
            nombre_archivo (str): Nombre del archivo Excel a cargar
                (solo el nombre, no la ruta completa).

        Returns:
            pd.DataFrame | None: DataFrame cargado, o None si no se encontró el archivo.
        """
        ruta_completa = os.path.join(self.carpeta_salida, nombre_archivo)
        if os.path.exists(ruta_completa):
            print(f"Cargando datos revisados desde {nombre_archivo}...")
            return pd.read_excel(ruta_completa)
        else:
            print(f"No se encontró el archivo en {ruta_completa}")
            return None
