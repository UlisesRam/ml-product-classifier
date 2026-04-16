# -*- coding: utf-8 -*-
"""
conexionesapis.py
=================
Módulo de conexión y comunicación con el motor de búsqueda (Lucidworks Fusion)
mediante su API REST.

Propósito:
    Automatizar la descarga, modificación y carga de reglas de elevación (query-rewrite)
    que controlan el orden de los productos mostrados en el buscador de un e-commerce.

Flujo general:
    1. Autenticación con credenciales del analista vía HTTP Basic Auth.
    2. Descarga de las reglas activas en producción.
    3. Actualización local con los nuevos listados de SKUs calculados.
    4. Validación de integridad antes de subir cambios.
    5. Carga a producción y auditoría post-carga.
"""

import requests
import base64
import pandas as pd
import ast
import json
import copy
from datetime import datetime, timezone


class ApisConnections:
    """
    Gestiona la conexión y las operaciones CRUD contra la API del motor de búsqueda.

    Attributes:
        url_lw (str): URL base del motor de búsqueda (Lucidworks Fusion).
        session (requests.Session): Sesión HTTP persistente con headers de autenticación.
        lw_endpoint (str): Endpoint específico para las reglas de query-rewrite.
    """

    def __init__(self, url_lw="https://[SEARCH_ENGINE_URL]", url_domo=None):
        """
        Inicializa la conexión con las URLs base del motor de búsqueda.

        Args:
            url_lw (str): URL base del motor de búsqueda.
            url_domo (str, optional): URL base del sistema de reportería (ej. Domo).
        """
        self.url_lw = url_lw
        self.url_domo = url_domo
        self.session = requests.Session()
        # Endpoint de reglas de query-rewrite para la aplicación del e-commerce
        self.lw_endpoint = f'{self.url_lw}/api/apps/[APP_NAME]/query-rewrite/instances'

    def authenticate_lw(self):
        """
        Solicita credenciales al analista de forma interactiva y configura
        la autenticación HTTP Basic Auth en la sesión.

        Las credenciales se codifican en Base64 y se almacenan solo en memoria
        durante la sesión, sin persistirse en disco.
        """
        username = input('Ingresa tu usuario del motor de búsqueda: \n')
        password = input('Ingresa tu contraseña: \n')

        # Codificación Base64 para HTTP Basic Auth (RFC 7617)
        auth_str = f"{username}:{password}"
        encode_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')

        self.session.headers.update({"Authorization": f"Basic {encode_auth}"})
        self.session.headers.update({"Content-Type": "application/json"})
        print(f'Autenticación cargada para {username}')

    def query(self, tag='TAG_EJEMPLO'):
        """
        Descarga todas las reglas activas del motor de búsqueda filtradas por tag.

        Los tags agrupan reglas por tipo de proceso (ej. términos orgánicos,
        campañas, etc.), permitiendo operar sobre un subconjunto específico.

        Args:
            tag (str): Etiqueta para filtrar las reglas. Default: 'TAG_EJEMPLO'.

        Returns:
            pd.DataFrame: DataFrame con todas las reglas activas del tag indicado.
                          Vacío si ocurre un error de conexión.
        """
        endpoint = self.lw_endpoint
        params = {"tags": tag, "limit": 10000}
        try:
            response = self.session.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()
            # El motor puede devolver lista directa o dict con 'items'/'docs' según versión
            df = pd.DataFrame(data) if isinstance(data, list) else \
                 pd.DataFrame(data.get('items', data.get('docs', [])))
            print(f'Se descargaron {len(df)} reglas del motor de búsqueda (Tag: {tag})')
            return df
        except Exception as e:
            print(f'Error al obtener las reglas: {e}')
            return pd.DataFrame()

    def _get_iso_date(self):
        """
        Genera la fecha y hora actual en formato ISO 8601 UTC, requerido
        por el motor de búsqueda para el campo 'updatedOn' de cada regla.

        Returns:
            str: Timestamp en formato 'YYYY-MM-DDTHH:MM:SS.mmmZ'.
        """
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    def actualizar_reglas_localmente(self, df_nuevos_listados, col_id_match,
                                     col_listado_nuevo, tag='TAG_EJEMPLO'):
        """
        Descarga las reglas actuales de producción, las cruza con los nuevos
        listados de SKUs calculados, aplica validaciones y recortes, y prepara
        el DataFrame final listo para subir.

        El proceso incluye tres fases:
            1. Validaciones de entrada (IDs duplicados, IDs inexistentes en producción).
            2. Procesamiento y recorte de SKUs al límite máximo permitido por el motor.
            3. Auditoría de integridad interna para confirmar que los cambios son correctos.

        Args:
            df_nuevos_listados (pd.DataFrame): DataFrame con los listados calculados.
                Debe contener al menos la columna de ID y la columna del nuevo listado.
            col_id_match (str): Nombre de la columna que contiene el ID de la regla.
            col_listado_nuevo (str): Nombre de la columna con el listado de SKUs (CSV).
            tag (str): Tag de las reglas a actualizar en producción.

        Returns:
            pd.DataFrame: Subconjunto de las reglas de producción con los params
                          ya actualizados, listo para enviarse a upload_changes().

        Raises:
            Exception: Si hay IDs duplicados en la entrada, IDs inexistentes en
                       producción, o si falla la auditoría de integridad.
        """
        # 1. Obtener datos frescos del motor de búsqueda
        df_motor = self.query(tag=tag)
        if df_motor.empty:
            raise Exception("Error crítico: No se pudieron obtener reglas del motor. "
                            "Verifica conexión o Tag.")

        # --- FASE 1: VALIDACIONES DE ENTRADA ---
        print("\nIniciando validaciones previas...")

        # Detectar IDs duplicados en el listado de entrada
        dups = df_nuevos_listados[df_nuevos_listados.duplicated(subset=[col_id_match], keep=False)]
        if not dups.empty:
            print(f"ERROR: IDs duplicados en el listado de entrada:")
            print(dups[[col_id_match]].head())
            raise Exception("Proceso detenido por IDs duplicados en la entrada.")

        # Verificar que todos los IDs del listado existan en producción
        ids_entrada = set(df_nuevos_listados[col_id_match].unique())
        ids_motor = set(df_motor['id'].unique())
        ids_inexistentes = ids_entrada - ids_motor

        if ids_inexistentes:
            raise Exception(
                f"Proceso detenido: Los siguientes IDs no existen en producción "
                f"(Tag: {tag}): {ids_inexistentes}"
            )

        # --- FASE 2: PROCESAMIENTO Y RECORTE DE SKUs ---
        mapeo_updates = dict(zip(df_nuevos_listados[col_id_match],
                                  df_nuevos_listados[col_listado_nuevo]))
        current_date = self._get_iso_date()

        # Límite máximo de SKUs permitido por el motor de búsqueda por regla
        MAX_SKUS_POR_REGLA = 900

        def procesar_fila_completa(row):
            """Actualiza los params de una regla con el nuevo listado de SKUs."""
            rule_id = row['id']

            if rule_id not in mapeo_updates:
                return row  # Si no hay actualización para esta regla, la dejamos igual

            # Limpiar, deduplicar y recortar la lista de SKUs
            valor_raw = str(mapeo_updates[rule_id])
            lista_skus = [s.strip() for s in valor_raw.split(',') if s.strip()]
            lista_skus = list(dict.fromkeys(lista_skus))  # Eliminar duplicados respetando orden

            if len(lista_skus) > MAX_SKUS_POR_REGLA:
                print(f"La regla {rule_id} supera el límite ({len(lista_skus)} SKUs) "
                      f"y se recortará a {MAX_SKUS_POR_REGLA}.")
                lista_skus = lista_skus[:MAX_SKUS_POR_REGLA]

            nuevo_valor = ",".join(lista_skus)

            # Normalización defensiva del campo 'params' (puede llegar como str o NaN)
            params = copy.deepcopy(row['params'])
            if isinstance(params, str):
                try:
                    params = ast.literal_eval(params)
                except:
                    params = []
            if not isinstance(params, list):
                params = []

            # Buscar el campo 'elevateIds' dentro de params y actualizarlo
            encontrado = False
            for d in params:
                if isinstance(d, dict) and d.get('key') == 'elevateIds':
                    d['value'] = nuevo_valor
                    encontrado = True
                    break

            # Si la regla no tenía 'elevateIds', lo creamos
            if not encontrado:
                params.append({
                    'key': 'elevateIds',
                    'value': nuevo_valor,
                    'policy': 'append'
                })

            row['params'] = params
            row['updatedOn'] = current_date
            return row

        # Aplicar la actualización a todo el DataFrame de reglas
        df_motor = df_motor.apply(procesar_fila_completa, axis=1)

        # Filtrar solo las reglas que vamos a subir
        df_para_subir = df_motor[df_motor['id'].isin(ids_entrada)].copy()

        # --- FASE 3: AUDITORÍA DE INTEGRIDAD INTERNA ---
        # Verificar que el campo 'elevateIds' esté presente en cada regla procesada
        print("Validando integridad de los JSONs generados...")
        for _, row in df_para_subir.iterrows():
            valor_final = next(
                (p['value'] for p in row['params'] if p.get('key') == 'elevateIds'),
                None
            )
            if valor_final is None:
                raise Exception(
                    f"Error de integridad en regla {row['id']}: "
                    f"el campo elevateIds desapareció después del procesamiento."
                )

        print("-" * 50)
        print(f"{len(df_para_subir)} reglas procesadas. Fecha actualizada: {current_date}")
        print("-" * 50)

        return df_para_subir

    def upload_changes(self, df_to_upload):
        """
        Sube los cambios procesados a producción mediante PUT requests individuales
        por cada regla, generando un reporte detallado del resultado de cada carga.

        El método maneja correctamente valores NaN del DataFrame (que causarían
        errores de serialización JSON) eliminándolos antes de cada request.

        Args:
            df_to_upload (pd.DataFrame): DataFrame devuelto por
                actualizar_reglas_localmente(), con las reglas ya procesadas.

        Returns:
            pd.DataFrame: Reporte de carga con columnas:
                - id: identificador de la regla
                - status_code: código HTTP de respuesta
                - resultado: 'Exitoso', 'Error' o 'Excepción'
                - mensaje: detalle del error si lo hubo
        """
        if df_to_upload is None or df_to_upload.empty:
            print("El DataFrame está vacío, no hay nada que subir.")
            return pd.DataFrame()

        print(f"Iniciando carga de {len(df_to_upload)} reglas al motor de búsqueda...")

        reporte_carga = []
        count = 0

        # Columnas internas del pipeline que no deben enviarse al motor
        columnas_prohibidas = ['listado_nuevo', '_merge', 'comentarios', 'check', 'product_list']

        for index, row in df_to_upload.iterrows():
            rule_id = row['id']
            endpoint = f'{self.lw_endpoint}/{rule_id}'

            # Eliminar columnas internas del pipeline
            for col in columnas_prohibidas:
                if col in row:
                    row = row.drop(col)

            # .dropna() elimina valores NaN que causarían error de serialización JSON
            payload = row.dropna().to_dict()

            resultado = {
                'id': rule_id,
                'status_code': 0,
                'resultado': 'Pendiente',
                'mensaje': ''
            }

            try:
                response = self.session.put(endpoint, json=payload)
                resultado['status_code'] = response.status_code

                if response.status_code in [200, 204]:
                    resultado['resultado'] = 'Exitoso'
                    resultado['mensaje'] = 'OK'
                else:
                    resultado['resultado'] = 'Error'
                    try:
                        error_msg = response.json().get('message', response.text)
                    except:
                        error_msg = response.text
                    resultado['mensaje'] = error_msg
                    print(f"Falló regla {rule_id} ({response.status_code}): {error_msg[:100]}")

            except Exception as e:
                resultado['resultado'] = 'Excepción'
                resultado['mensaje'] = str(e)
                print(f"Error técnico en regla {rule_id}: {str(e)}")

            reporte_carga.append(resultado)

            count += 1
            if count % 20 == 0:
                print(f"   ... procesadas {count}/{len(df_to_upload)}")

        df_reporte = pd.DataFrame(reporte_carga)
        exitosos = df_reporte[df_reporte['resultado'] == 'Exitoso'].shape[0]
        errores = df_reporte[df_reporte['resultado'] != 'Exitoso'].shape[0]

        print("\n" + "=" * 30)
        print("CARGA TERMINADA")
        print(f"Exitosos : {exitosos}")
        print(f"Errores  : {errores}")
        print("=" * 30)

        return df_reporte

    def validar_carga_vs_productivo(self, df_intencion_carga, tag='TAG_EJEMPLO'):
        """
        Auditoría post-carga: descarga nuevamente las reglas de producción y
        compara los valores de 'elevateIds' contra lo que se intentó subir,
        detectando discrepancias o pérdidas de datos.

        Se ejecuta después de upload_changes() para confirmar que los cambios
        llegaron correctamente a producción.

        Args:
            df_intencion_carga (pd.DataFrame): DataFrame procesado localmente
                (salida de actualizar_reglas_localmente).
            tag (str): Tag a consultar en producción para la comparación.

        Returns:
            pd.DataFrame: Reporte de validación con columnas:
                - id: identificador de la regla
                - estatus_validacion: 'OK', 'DIFERENCIA DETECTADA', 'ERROR FATAL',
                  o 'FECHA DIFERENTE'
                - detalle: descripción del problema si lo hay
        """
        print("\nIniciando auditoría post-carga (comparando contra producción)...")

        # Descargar el estado actual de producción
        df_vivo = self.query(tag=tag)
        if df_vivo.empty:
            print("No se pudo descargar producción para comparar.")
            return None

        resultados_validacion = []

        for _, row_local in df_intencion_carga.iterrows():
            r_id = row_local['id']
            match_vivo = df_vivo[df_vivo['id'] == r_id]

            status_val = "OK"
            nota = ""

            if match_vivo.empty:
                status_val = "ERROR FATAL"
                nota = "La regla desapareció de producción."
            else:
                row_vivo = match_vivo.iloc[0]

                # --- Extraer elevateIds local (con manejo de NaN y strings) ---
                params_local = row_local.get('params', [])
                if isinstance(params_local, float):
                    params_local = []
                if isinstance(params_local, str):
                    try:
                        params_local = ast.literal_eval(params_local)
                    except:
                        params_local = []

                val_local = next(
                    (p['value'] for p in params_local
                     if isinstance(p, dict) and p.get('key') == 'elevateIds'),
                    ""
                )

                # --- Extraer elevateIds de producción (con manejo de NaN) ---
                params_vivo = row_vivo.get('params', [])
                if pd.isna(params_vivo) or params_vivo is None:
                    params_vivo = []
                elif isinstance(params_vivo, str):
                    try:
                        params_vivo = ast.literal_eval(params_vivo)
                    except:
                        params_vivo = []
                if not isinstance(params_vivo, list):
                    params_vivo = []

                val_vivo = next(
                    (p['value'] for p in params_vivo
                     if isinstance(p, dict) and p.get('key') == 'elevateIds'),
                    ""
                )

                # Comparar valores
                if str(val_local) != str(val_vivo):
                    status_val = "DIFERENCIA DETECTADA"
                    nota = (f"Esperado: {str(val_local)[:20]}... "
                            f"| En Vivo: {str(val_vivo)[:20]}...")
                elif str(row_local.get('updatedOn')) != str(row_vivo.get('updatedOn')):
                    status_val = "FECHA DIFERENTE"
                    nota = (f"Datos ok, fecha difiere. "
                            f"Local: {row_local.get('updatedOn')} "
                            f"vs Vivo: {row_vivo.get('updatedOn')}")

            resultados_validacion.append({
                'id': r_id,
                'estatus_validacion': status_val,
                'detalle': nota
            })

        df_val = pd.DataFrame(resultados_validacion)
        errores_reales = df_val[~df_val['estatus_validacion'].isin(['OK', 'FECHA DIFERENTE'])]

        if not errores_reales.empty:
            print(f"ATENCION: Se encontraron {len(errores_reales)} discrepancias graves.")
        else:
            print("Exito Total. Todas las reglas en produccion coinciden con la carga.")

        return df_val
