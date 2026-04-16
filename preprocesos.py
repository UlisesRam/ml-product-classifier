# -*- coding: utf-8 -*-
"""
preprocesos.py
==============
Módulo de preprocesamiento y sincronización de datos para el pipeline
de gestión de términos orgánicos del buscador de e-commerce.

Propósito:
    Mantener sincronizados los diccionarios de términos entre distintos
    sistemas (Google Sheets), gestionar el histórico de decisiones de
    clasificación y detectar automáticamente nuevos archivos de trabajo.

Responsabilidades:
    - Sincronizar el listado de términos activos entre el diccionario
      maestro y el dashboard de seguimiento.
    - Construir y actualizar el historial acumulado de decisiones
      (aprobado/rechazado por SKU-término), que alimenta al modelo
      de clasificación automática.
    - Localizar automáticamente el archivo de trabajo más reciente
      en una carpeta de destino.

Dependencias:
    gspread         - Lectura/escritura de Google Sheets
    google.colab    - Autenticación OAuth en entorno Colab
    pandas          - Manipulación de DataFrames
"""

import gspread
from google.colab import auth
from google.auth import default
import pandas as pd
import os
import re
from datetime import datetime


class PreProcesos:
    """
    Gestiona la autenticación con Google y las operaciones de sincronización
    y mantenimiento de datos entre Google Sheets y archivos locales/Drive.

    Al instanciarse, realiza el flujo de autenticación OAuth de Google
    automáticamente, por lo que debe ejecutarse en un entorno que lo soporte
    (Google Colab o equivalente).
    """

    def __init__(self):
        """
        Inicializa la clase autenticando al usuario con Google OAuth y
        creando el cliente de gspread para operar con Sheets.
        """
        auth.authenticate_user()
        creds, _ = default()
        self.client = gspread.authorize(creds)

    def sync_variants(self, spreadsheet_dict, sheet_dict, spreadsheet_dash,
                      sheet_dash, col_name, col_val, valid):
        """
        Sincroniza el listado de términos activos entre el diccionario maestro
        y el dashboard de seguimiento semanal.

        Compara los términos marcados como válidos en el diccionario contra
        los que están registrados en el dashboard. Si hay diferencias, actualiza
        el dashboard agregando los nuevos y eliminando los obsoletos.

        Esto garantiza que ambos sistemas siempre operen sobre el mismo
        conjunto de términos, evitando inconsistencias manuales.

        Args:
            spreadsheet_dict (str): Nombre del Google Sheet del diccionario maestro.
            sheet_dict (str): Nombre de la hoja dentro del diccionario maestro.
            spreadsheet_dash (str): Nombre del Google Sheet del dashboard.
            sheet_dash (str): Nombre de la hoja dentro del dashboard.
            col_name (str): Columna del diccionario que contiene el nombre del término.
            col_val (str): Columna del diccionario que contiene el estado de validez.
            valid (str): Valor que indica que un término está activo (ej. 'ok').
        """
        # Cargar diccionario maestro y filtrar solo los términos activos
        sh_dic = self.client.open(spreadsheet_dict).worksheet(sheet_dict)
        data = sh_dic.get_all_values()
        df_dic = pd.DataFrame(data[1:], columns=data[0])
        df_dic = df_dic.loc[:, df_dic.columns != '']
        df_dic = df_dic[
            df_dic[col_val].astype(str).str.lower().str.strip() == str(valid).lower().strip()
        ]

        # Cargar términos actuales del dashboard (columna A)
        sh_dash = self.client.open(spreadsheet_dash).worksheet(sheet_dash)
        df_dash_values = sh_dash.col_values(1)

        new_terms = {
            str(t).strip() for t in df_dic[col_name].unique()
            if t and str(t).strip() not in ['nan', 'None', '']
        }
        actual_terms = {
            str(t).strip() for t in df_dash_values[1:]
            if t and str(t).strip() not in ['nan', 'None', '']
        }

        if new_terms != actual_terms:
            print(f'Se detectaron cambios en el diccionario: {spreadsheet_dict}')

            if new_terms - actual_terms:
                print(f'Términos nuevos a agregar: {new_terms - actual_terms}')
            if actual_terms - new_terms:
                print(f'Términos a eliminar: {actual_terms - new_terms}')

            # Limpiar y reescribir la columna A del dashboard con los términos actualizados
            sh_dash.batch_clear(["A2:A"])
            up_list = [[t] for t in new_terms if t != 'nan']
            sh_dash.update('A2', up_list)

            if new_terms - actual_terms:
                print(f'Se agregaron {len(new_terms - actual_terms)} términos a {spreadsheet_dash}')
            if actual_terms - new_terms:
                print(f'Se eliminaron {len(actual_terms - new_terms)} términos de {spreadsheet_dash}')
        else:
            print(f'Sin cambios entre {spreadsheet_dict} y {spreadsheet_dash}. '
                  f'No se requiere actualización.')

    def hist_dict(self, spreadsheet_dict: str, sheet_dict: str, cols: list,
                  col_val: str, valid: str, col_ter: str):
        """
        Carga el diccionario maestro de términos, filtrando únicamente los
        registros activos y las columnas relevantes para el pipeline.

        Este DataFrame sirve como mapa de términos → analistas → IDs de reglas,
        permitiendo cruzar los datos de performance con las reglas del buscador.

        Args:
            spreadsheet_dict (str): Nombre del Google Sheet del diccionario maestro.
            sheet_dict (str): Nombre de la hoja a leer.
            cols (list): Lista de columnas a conservar en el resultado.
            col_val (str): Columna que contiene el estado de validez del término.
            valid (str): Valor que indica término activo (ej. 'ok').
            col_ter (str): Columna del nombre del término; se usa para eliminar
                           filas sin término asignado.

        Returns:
            pd.DataFrame: Diccionario filtrado con solo los términos activos
                          y las columnas solicitadas.
        """
        sh_dic = self.client.open(spreadsheet_dict).worksheet(sheet_dict)
        data = sh_dic.get_all_values()
        df_dic = pd.DataFrame(data[1:], columns=data[0])
        df_dic = df_dic.loc[:, df_dic.columns != '']

        # Filtrar términos activos y columnas requeridas
        df_dic = df_dic[
            df_dic[col_val].astype(str).str.lower().str.strip() == str(valid).lower().strip()
        ]
        df_dic = df_dic[cols]
        df_dic = df_dic.dropna(subset=[col_ter])

        return df_dic

    def _get_latest_file(self, folder_path,
                          pattern_prefix='Acomodos_terminos_organicos_',
                          num_archivos_anteriores=0):
        """
        Detecta automáticamente el archivo Excel más reciente (o N-ésimo anterior)
        en una carpeta, basándose en la fecha embebida en el nombre del archivo.

        Los archivos de trabajo siguen la convención de nombre:
            {pattern_prefix}DD-MM-YYYY.xlsx

        Esto permite al pipeline ubicar el archivo de la semana anterior sin
        necesidad de especificar el nombre manualmente.

        Args:
            folder_path (str): Ruta de la carpeta donde buscar.
            pattern_prefix (str): Prefijo que deben tener los archivos a considerar.
            num_archivos_anteriores (int): 0 = el más reciente, 1 = penúltimo, etc.

        Returns:
            str | None: Nombre del archivo encontrado, o None si no hay archivos
                        válidos o si no hay suficientes para el índice solicitado.
        """
        files = [
            f for f in os.listdir(folder_path)
            if f.endswith('.xlsx') and f.startswith(pattern_prefix)
        ]

        if not files:
            return None

        # Extraer la fecha del nombre del archivo y ordenar cronológicamente
        date_pattern = re.compile(r'(\d{2}-\d{2}-\d{4})')
        files_with_dates = []

        for file_name in files:
            match_d = date_pattern.search(file_name)
            if match_d:
                date_str = match_d.group(1)
                try:
                    file_date = datetime.strptime(date_str, '%d-%m-%Y')
                    files_with_dates.append((file_date, file_name))
                except ValueError:
                    continue

        if not files_with_dates:
            return None

        files_with_dates.sort(key=lambda x: x[0])

        # Índice negativo: -1 = más reciente, -2 = penúltimo, etc.
        index = -1 - num_archivos_anteriores

        try:
            return files_with_dates[index][1]
        except IndexError:
            print(f"No hay suficientes archivos para retroceder {num_archivos_anteriores} posiciones.")
            return None

    def update_historico_decisiones(self, path_csv_hist, folder_new_files,
                                     cols_to_keep, subset_dupes,
                                     rename_cols, col_to_standardize=None):
        """
        Actualiza el historial acumulado de decisiones de clasificación
        (SKU-término: Sí/No/Revisar) incorporando el archivo de la semana actual.

        El historial es el insumo principal del modelo de clasificación automática:
        a mayor historial, mejores predicciones. Este método garantiza que el CSV
        acumulado se mantenga limpio, sin duplicados y con texto estandarizado.

        Flujo:
            1. Leer el CSV histórico existente (o crear uno vacío si no existe).
            2. Detectar automáticamente el Excel más reciente en la carpeta.
            3. Confirmar con el usuario que es el archivo correcto.
            4. Concatenar, estandarizar texto y deduplicar.
            5. Sobreescribir el CSV solo si hay filas nuevas.

        Args:
            path_csv_hist (str): Ruta completa del CSV histórico acumulado.
            folder_new_files (str): Carpeta donde buscar el Excel más reciente.
            cols_to_keep (list): Columnas del Excel nuevo a incorporar.
            subset_dupes (list): Columnas que definen unicidad (ej. ['sku', 'termino_agg']).
            rename_cols (dict): Mapeo para renombrar columnas del Excel nuevo.
            col_to_standardize (str, optional): Columna de texto a normalizar
                (eliminar acentos, capitalizar) para evitar duplicados por variantes
                de escritura (ej. 'Si' vs 'sí' vs 'SI').

        Returns:
            pd.DataFrame: Histórico actualizado con todas las decisiones acumuladas.
        """
        # Paso 1: Cargar historial existente
        try:
            df_history = pd.read_csv(path_csv_hist)
            rows_before = len(df_history)
            print(f'Histórico cargado: {rows_before} filas.')
            df_history = df_history.drop_duplicates(subset=subset_dupes, keep='last')
            if len(df_history) < rows_before:
                print(f'Se encontraron y eliminaron duplicados en el histórico '
                      f'(columnas: {subset_dupes}).')
        except FileNotFoundError:
            print(f'No se encontró el archivo en {path_csv_hist}. Creando uno nuevo.')
            df_history = pd.DataFrame()
            rows_before = 0

        # Paso 2: Detectar el Excel más reciente en la carpeta de trabajo
        latest_filename = self._get_latest_file(folder_new_files)

        if not latest_filename:
            print('No se encontraron archivos nuevos.')
            return df_history

        print(f"\n{'=' * 40}\nArchivo detectado: {latest_filename}\n{'=' * 40}")
        respuesta = input('¿Es el archivo correcto? (s/n): ')

        if respuesta.lower() not in ['s', 'si', 'sí', 'y', 'yes']:
            return df_history

        path_new_file = os.path.join(folder_new_files, latest_filename)

        # Paso 3: Cargar y validar el Excel nuevo
        try:
            df_new = pd.read_excel(path_new_file)
            missing_cols = [col for col in cols_to_keep if col not in df_new.columns]
            if missing_cols:
                print(f'Error: Faltan columnas en el archivo nuevo: {missing_cols}')
                return df_history

            df_new = df_new[cols_to_keep].rename(columns=rename_cols)
            print(f'Columnas renombradas: {list(rename_cols.keys())} → '
                  f'{list(rename_cols.values())}')
        except Exception as e:
            print(f'Error al procesar el Excel: {e}')
            return df_history

        # Paso 4: Combinar histórico con datos nuevos
        df_combined = pd.concat([df_history, df_new], ignore_index=True)

        # Estandarizar la columna de decisiones para evitar duplicados por variantes de texto
        # Ejemplo: 'sí', 'SI', 'Sí' → 'Si' (elimina acentos y capitaliza)
        if col_to_standardize and col_to_standardize in df_combined.columns:
            print(f'Estandarizando columna: {col_to_standardize}...')
            df_combined[col_to_standardize] = (
                df_combined[col_to_standardize]
                .astype(str)
                .str.normalize('NFKD')               # Descomponer caracteres Unicode
                .str.encode('ascii', errors='ignore') # Eliminar diacríticos
                .str.decode('utf-8')
                .str.capitalize()
                .str.strip()
            )

        # Paso 5: Deduplicar (keep='last' para que el registro más reciente gane)
        df_final = df_combined.drop_duplicates(subset=subset_dupes, keep='last')

        rows_after = len(df_final)
        rows_added = rows_after - rows_before

        # Paso 6: Guardar solo si hubo cambios reales
        if rows_added > 0:
            df_final.to_csv(path_csv_hist, index=False)
            print(f'Éxito: Se agregaron {rows_added} filas. CSV actualizado.')
        else:
            print('No hay filas nuevas para agregar.')

        return df_final
