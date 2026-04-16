# -*- coding: utf-8 -*-
"""
listados.py
===========
Módulo de generación y cruce de listados de SKUs para las reglas
de elevación del buscador de e-commerce.

Propósito:
    Construir el listado final de SKUs ordenados que se cargará en cada
    regla del buscador, combinando dos fuentes de recomendación:

    1. Performance analítica: SKUs con mejor score de conversión/vistas,
       calculados semanalmente de forma automática.
    2. Apuestas de especialistas: SKUs seleccionados manualmente por el
       equipo analítico como prioritarios para cada término.

Lógica de mezcla:
    El listado final se construye en tres pasos:
        a) Extracción de SKUs fijos (apuestas prioritarias que van al frente).
        b) Mezcla intercalada de performance vs apuestas restantes con
           proporción configurable (ej. 2 de performance por 1 de apuesta).
        c) Separación y rebalanceo entre productos 1P (propios) y 3P
           (marketplace) según parámetros del diccionario.
"""

import pandas as pd
import re
import numpy as np


class GeneradorListados:
    """
    Genera los listados finales de SKUs para cada término del buscador,
    cruzando las recomendaciones de performance con las apuestas de especialistas.

    Attributes:
        client: Cliente de gspread para leer los Sheets de apuestas de especialistas.
        col_n_perf (str): Nombre de la columna con el número de SKUs de performance
                          a tomar en la mezcla.
        col_n_espec (str): Nombre de la columna con el número de SKUs de especialistas
                           a tomar en la mezcla.
        df_dict (pd.DataFrame): Diccionario maestro de términos con parámetros
                                de configuración de la mezcla.
    """

    def __init__(self, gspread_client=None, df_dict=None,
                 col_n_perf='n_performance', col_n_espec='n_especialistas'):
        """
        Inicializa el generador con el cliente de Sheets y el diccionario de términos.

        Args:
            gspread_client: Cliente autenticado de gspread.
            df_dict (pd.DataFrame, optional): Diccionario maestro. Se filtran y
                deduplicadas las columnas necesarias al inicializar.
            col_n_perf (str): Columna del diccionario con el número de SKUs de
                              performance por término.
            col_n_espec (str): Columna del diccionario con el número de SKUs de
                               especialistas por término.
        """
        self.client = gspread_client
        self.col_n_perf = col_n_perf
        self.col_n_espec = col_n_espec

        if df_dict is not None:
            columnas_necesarias = [
                'termino representativo', 'id', self.col_n_perf, self.col_n_espec,
                'cruce_1p', 'cruce_3p', 'cruces_1p_3p', 'listado_especial',
                'espacios_especiales'
            ]
            cols_presentes = [c for c in columnas_necesarias if c in df_dict.columns]
            self.df_dict = (
                df_dict[cols_presentes]
                .drop_duplicates(subset='termino representativo')
                .copy()
            )
            self.df_dict['termino representativo'] = (
                self.df_dict['termino representativo'].str.lower().str.strip()
            )
        else:
            self.df_dict = None

    def _listado_performance(self, df_performance, col_decision_final):
        """
        Filtra y agrupa los SKUs aprobados del cálculo de performance,
        generando una lista CSV ordenada por sequence_final para cada término.

        Solo se incluyen SKUs con decisión 'Si' y se respeta el orden
        calculado por el score de performance.

        Args:
            df_performance (pd.DataFrame): Resultado de CalculosPerformance.calcular_performance().
            col_decision_final (str): Nombre de la columna con la decisión (Si/No/Revisar).

        Returns:
            pd.DataFrame: Un registro por término con columnas:
                - termino_agg: nombre del término
                - sku: string CSV con los SKUs aprobados en orden de performance
                - id: ID de la regla en el motor de búsqueda
        """
        df_valido = df_performance[df_performance[col_decision_final] == 'Si'].copy()
        df_valido = (
            df_valido
            .drop_duplicates(subset=['termino_agg', 'sku'])
            .sort_values(by=['termino_agg', 'sequence_final'])
        )
        df_valido['sku'] = df_valido['sku'].astype(str)
        df_agrupado = df_valido.groupby('termino_agg').agg({
            'sku': ','.join,
            'id': 'first'
        })
        df_agrupado.index = df_agrupado.index.astype(str).str.lower().str.strip()
        return df_agrupado.reset_index()

    def _obtener_penultima_semana(self, spreadsheet_name, cantidad_semanas):
        """
        Determina qué hoja del Google Sheet de apuestas corresponde a la semana
        objetivo (semana actual - cantidad_semanas).

        Las hojas del archivo de apuestas siguen la convención de nombre:
        'Carga N', donde N es el número de carga semanal. El método detecta
        automáticamente el número máximo y retrocede N semanas.

        Args:
            spreadsheet_name (str): Nombre del Google Sheet de apuestas.
            cantidad_semanas (int): Cuántas semanas hacia atrás ir.
                0 = semana actual, 1 = semana anterior, etc.

        Returns:
            str | None: Nombre de la hoja objetivo, o None si no existe.
        """
        sh = self.client.open(spreadsheet_name)
        hojas = [ws.title for ws in sh.worksheets()]

        numeros_semana = []
        for nombre in hojas:
            match = re.search(r'Carga\s*(\d+)', nombre, re.IGNORECASE)
            if match:
                numeros_semana.append(int(match.group(1)))

        if not numeros_semana:
            return None

        max_sem = max(numeros_semana)
        print(f'Última carga disponible en apuestas: {max_sem}')
        semana_objetivo = max_sem - cantidad_semanas
        nombre_final = f"Carga {semana_objetivo}"
        return nombre_final if nombre_final in hojas else None

    def _aux_especialistas(self, spreadsheet_name, cant_semanas):
        """
        Lee la hoja de apuestas de especialistas, valida el formato de los SKUs
        y retorna el DataFrame limpio.

        Valida que cada SKU cumpla con uno de los formatos permitidos:
            - PM-XXXXXXX3  (producto propio, área muebles)
            - PR-XXXXXXX2  (producto propio, área ropa/calzado)
            - MKP-XXXXXXXX (producto marketplace)

        Si se detectan SKUs con formato inválido, se imprime un reporte
        detallado por término y se retorna None para detener el proceso.

        Args:
            spreadsheet_name (str): Nombre del Google Sheet de apuestas.
            cant_semanas (int): Semanas hacia atrás a leer.

        Returns:
            pd.DataFrame | None: DataFrame con apuestas válidas, o None si hay errores.
        """
        if not self.client:
            return None

        nombre_hoja = self._obtener_penultima_semana(spreadsheet_name, cant_semanas)
        print(f'Leyendo hoja de apuestas: {nombre_hoja}')
        if not nombre_hoja:
            return None

        sh = self.client.open(spreadsheet_name).worksheet(nombre_hoja)
        data = sh.get('A:H')
        df = pd.DataFrame(data[1:], columns=data[0])

        # Limpiar valores vacíos y espacios en la columna de SKU
        df['SKU'] = df['SKU'].replace(r'^\s*$', np.nan, regex=True)
        df = df.dropna(subset=['SKU', 'Term Representativo']).copy()
        df['SKU'] = df['SKU'].astype(str).str.strip().str.replace(',', '')

        # Validar formato de SKU: PM-XXXXXXX3, PR-XXXXXXX2 o MKP-XXXXXXXX
        patron_sku = r'^(PM-\d{3,}3|PR-\d{3,}2|MKP-\d{4,})$'
        df['es_valido'] = df['SKU'].str.match(patron_sku)

        df_errores = df[df['es_valido'] == False]
        if not df_errores.empty:
            print("\n" + "!" * 60)
            print("SKUS CON FORMATO INCORRECTO DETECTADOS")
            errores_detallados = (
                df_errores.groupby('Term Representativo')['SKU'].apply(list).to_dict()
            )
            for term, skus in errores_detallados.items():
                print(f"Término: {term} -> SKUs inválidos: {skus}")
            print("!" * 60 + "\n")
            return None

        return df

    def _listados_especialistas(self, spreadsheet_name, cant_semanas):
        """
        Obtiene y agrupa las apuestas de especialistas por término representativo,
        generando una lista CSV de SKUs por término.

        Args:
            spreadsheet_name (str): Nombre del Google Sheet de apuestas.
            cant_semanas (int): Semanas hacia atrás a leer.

        Returns:
            pd.DataFrame | None: DataFrame con columnas 'Term Representativo'
                y 'SKU' (CSV), o None si hubo errores en la validación.
        """
        df = self._aux_especialistas(spreadsheet_name, cant_semanas)
        if df is None:
            return None
        df_agrupado = (
            df.groupby('Term Representativo')
            .agg({'SKU': ','.join})
            .reset_index()
        )
        df_agrupado['Term Representativo'] = (
            df_agrupado['Term Representativo'].str.lower().str.strip()
        )
        return df_agrupado

    def mezclar_listados(self, row, col_a, col_b, take_a, take_b,
                          cruce_1p3p='si', col_prefinal=None,
                          list_especial='no', skus_esp=0):
        """
        Combina dos listas de SKUs de forma intercalada según proporciones configuradas.

        La mezcla sigue un patrón de intercalado: toma take_a elementos de
        la lista A, luego take_b de la lista B, y repite hasta agotar ambas.
        Se garantiza que no haya duplicados en el resultado.

        Opcionalmente, si el término tiene un 'listado especial', los primeros
        skus_esp SKUs de la lista B se insertan al principio del resultado
        (antes del intercalado), asegurando su posición fija en el buscador.

        Ejemplo con take_a=2, take_b=1:
            Lista A: [P1, P2, P3, P4]  (performance)
            Lista B: [E1, E2]           (especialistas)
            Resultado: [P1, P2, E1, P3, P4, E2]

        Args:
            row (pd.Series): Fila del DataFrame con los valores de las listas.
            col_a (str): Columna con la lista A (ej. performance) en formato CSV.
            col_b (str): Columna con la lista B (ej. apuestas) en formato CSV.
            take_a (int): Cuántos SKUs tomar de A en cada ciclo de intercalado.
            take_b (int): Cuántos SKUs tomar de B en cada ciclo de intercalado.
            cruce_1p3p (str): 'si' para ejecutar la mezcla, 'no' para retornar
                              col_prefinal sin modificar.
            col_prefinal (str, optional): Valor a retornar cuando cruce_1p3p='no'.
            list_especial (str): 'si' si el término tiene SKUs fijos prioritarios.
            skus_esp (int): Cantidad de SKUs de lista_b a insertar al inicio.

        Returns:
            str: Lista final de SKUs en formato CSV, sin duplicados.
        """
        if cruce_1p3p == 'si':
            try:
                t_a = int(take_a) if pd.notnull(take_a) else 1
                t_b = int(take_b) if pd.notnull(take_b) else 1
            except:
                t_a, t_b = 2, 1

            val_a = str(row[col_a]) if pd.notnull(row[col_a]) else ""
            val_b = str(row[col_b]) if pd.notnull(row[col_b]) else ""

            lista_a = [s.strip() for s in val_a.split(',') if s.strip() and s.strip().lower() != 'nan']
            lista_b = [s.strip() for s in val_b.split(',') if s.strip() and s.strip().lower() != 'nan']

            resultado, i, j = [], 0, 0

            # Insertar SKUs fijos al inicio (apuestas prioritarias, posición garantizada)
            if str(list_especial).lower() == 'si' and skus_esp > 0:
                for _ in range(min(len(lista_b), skus_esp)):
                    if lista_b[j] not in resultado:
                        resultado.append(lista_b[j])
                    j += 1

            # Intercalado principal: take_a de A, luego take_b de B, repetir
            while i < len(lista_a) or j < len(lista_b):
                for _ in range(t_a):
                    if i < len(lista_a):
                        if lista_a[i] not in resultado:
                            resultado.append(lista_a[i])
                        i += 1
                for _ in range(t_b):
                    if j < len(lista_b):
                        if lista_b[j] not in resultado:
                            resultado.append(lista_b[j])
                        j += 1

            return ",".join(resultado)
        else:
            return col_prefinal

    def extraer_fijos(self, row):
        """
        Para términos con 'listado especial', separa los SKUs que van
        fijos al inicio del listado final de los que entran en la mezcla normal.

        Algunos términos estratégicos requieren que ciertos productos aparezcan
        siempre en las primeras posiciones, independientemente del score de
        performance. Esta función extrae esos SKUs prioritarios.

        Args:
            row (pd.Series): Fila del DataFrame con los campos 'listado_especial',
                'espacios_especiales' y 'SKU'.

        Returns:
            tuple(str, str): (skus_fijos, skus_restantes) ambos en formato CSV.
                - skus_fijos: los primeros N SKUs que van al frente del listado.
                - skus_restantes: los SKUs que siguen participando en la mezcla.
        """
        term_actual = str(row.get('termino representativo', 'N/A'))
        valor_especial = str(row.get('listado_especial', 'no'))

        if valor_especial == 'si':
            skus_esp = int(row.get('espacios_especiales', 0))
            val_b = str(row.get('SKU', ""))

            if not val_b or val_b == "":
                print(f"[{term_actual}]: Marcado como especial pero sin SKUs en la columna de apuestas.")
                return "", ""

            lista_b = [s.strip() for s in val_b.split(',') if s.strip()]
            fijos = lista_b[:skus_esp]
            restantes = lista_b[skus_esp:]

            print(f"[{term_actual}]: Extrayendo {skus_esp} SKUs fijos. "
                  f"Encontrados: {len(fijos)}. (SKUs: {fijos})")
            return ",".join(fijos), ",".join(restantes)

        return "", str(row.get('SKU', ""))

    def cruce_de_listados(self, spreadsheet_apuestas, df_performance,
                           col_decision_final, default_n_perf=1,
                           default_n_espec=2, c_semanas=1):
        """
        Orquesta el proceso completo de generación de listados finales,
        ejecutando los cinco pasos del pipeline de mezcla.

        Pipeline:
            Paso 1: Separar SKUs fijos (apuestas prioritarias).
            Paso 2: Mezcla intercalada de performance vs apuestas restantes.
            Paso 3: Separar el resultado en productos 1P (propios) y 3P (marketplace).
            Paso 4: Rebalancear proporción 1P/3P según parámetros del diccionario.
            Paso 5: Ensamblar listado final: fijos + mezcla rebalanceada.

        Args:
            spreadsheet_apuestas (str): Nombre del Google Sheet de apuestas semanales.
            df_performance (pd.DataFrame): Resultado clasificado de CalculosPerformance.
            col_decision_final (str): Columna con la decisión final (Si/No).
            default_n_perf (int): SKUs de performance por ciclo si no está en el diccionario.
            default_n_espec (int): SKUs de especialistas por ciclo si no está en el diccionario.
            c_semanas (int): Semanas hacia atrás para leer las apuestas.

        Returns:
            pd.DataFrame | None: DataFrame con el listado final por término,
                o None si hubo errores al leer las apuestas.
        """
        df_apuestas = self._listados_especialistas(
            spreadsheet_name=spreadsheet_apuestas, cant_semanas=c_semanas
        )
        df_perf_agrupado = self._listado_performance(df_performance, col_decision_final)

        if df_apuestas is None:
            return None

        # Merge triple: diccionario + performance + apuestas
        df_completo = self.df_dict.merge(
            df_perf_agrupado,
            left_on='termino representativo', right_on='termino_agg', how='left'
        )
        df_completo = df_completo.merge(
            df_apuestas,
            left_on='termino representativo', right_on='Term Representativo', how='left'
        )

        # Limpieza de tipos antes del apply para evitar errores en filas sin datos
        df_completo['espacios_especiales'] = (
            pd.to_numeric(df_completo['espacios_especiales'], errors='coerce')
            .fillna(0).astype(int)
        )
        df_completo['listado_especial'] = (
            df_completo['listado_especial'].astype(str).str.lower().str.strip().fillna('no')
        )
        df_completo['SKU'] = df_completo['SKU'].astype(str).replace(['nan', 'None', ''], '')

        # Paso 1: Separar SKUs fijos de los que van a la mezcla
        df_res = df_completo.apply(self.extraer_fijos, axis=1, result_type='expand')
        df_completo['listado_fijo_inicial'] = df_res[0]
        df_completo['SKU_disponibles'] = df_res[1]

        # Paso 2: Mezcla intercalada performance vs apuestas restantes
        df_completo['listado_prefinal'] = df_completo.apply(
            lambda s: self.mezclar_listados(
                s, 'sku', 'SKU_disponibles',
                s.get(self.col_n_perf, default_n_perf),
                s.get(self.col_n_espec, default_n_espec)
            ), axis=1
        )

        # Paso 3: Separar productos 1P (PM/PR) de 3P (MKP)
        df_completo['lista_pre_aux'] = df_completo['listado_prefinal'].astype(str).str.split(',')
        df_completo['skus_1p'] = df_completo['lista_pre_aux'].apply(
            lambda lista: ','.join(
                s.strip() for s in lista if s.strip().startswith(('PM', 'PR'))
            )
        )
        df_completo['skus_3p'] = df_completo['lista_pre_aux'].apply(
            lambda lista: ','.join(
                s.strip() for s in lista if s.strip().startswith('MKP')
            )
        )

        # Paso 4: Rebalancear proporción 1P/3P según configuración del diccionario
        df_completo['mezcla_dinamica'] = df_completo.apply(
            lambda s: self.mezclar_listados(
                s, col_a='skus_1p', col_b='skus_3p',
                take_a=s.get('cruce_1p', 1),
                take_b=s.get('cruce_3p', 1),
                cruce_1p3p=str(s.get('cruces_1p_3p', 'no')).lower(),
                col_prefinal=s['listado_prefinal']
            ), axis=1
        )

        # Paso 5: Ensamblar listado final: fijos al frente + mezcla rebalanceada
        def ensamblar_final(row):
            fijos = row['listado_fijo_inicial']
            mezcla = row['mezcla_dinamica']
            if fijos:
                return f"{fijos},{mezcla}".strip(',') if mezcla else fijos
            return mezcla

        df_completo['listado_final'] = df_completo.apply(ensamblar_final, axis=1)

        # Retornar solo los términos que tienen al menos un SKU en el listado final
        return df_completo[df_completo['listado_final'] != ""].copy()
