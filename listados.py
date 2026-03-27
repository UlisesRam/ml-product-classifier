
'''
La finalidad de esta clase es leer el archivo diccionario en el que se encuentran especificaciones por termino argupador,
y por cada termino agrupador unico leer el listado concatenado de skus que lanzo el performance con los mejores de la semana,
leer los skus que el equipo interno determina que son mejores para mostrar y hacer un mix de ambos listados para impulsar el performance
semana con semana.
'''
import pandas as pd
import re
import numpy as np

class GeneradorListados:
    def __init__(self, gspread_client, df_dict, col_n_perf, col_n_espec):
        '''Se debe instanciar la clase con al menos la data necesaria por termino agrupador, que son justamente el diccionario
        de donde salen las especs por termino, las columnas de como se va a hacer el cruce y adicionalmente una carga del cliente
        en drive para no estar loggeando cada vez.
        Las variables necesarias a instanciar son:
        gspread_client: obj |   Se mete de una autenticacion en drive hecha con anterioridad
        df_dict: DataFrame  |   Se debe ingestar un dataframe con las caracteristicas necesarias por termino agrupador
        col_n_perf: str     |   El nombre de columna en la que se pone el numero por termino agrupador que va a respetar el mix de performance
                                es decir si van a ser 2 skus de perf y 1 de apuesta, el numero dos corresponde a esta col
        col_n_espec: str    |   Analogo a la de performance esta columna contiene el numero de cruce para apuestas 
        
        '''
        self.client = gspread_client
        self.col_n_perf = col_n_perf
        self.col_n_espec = col_n_espec

        if df_dict is not None:
            columnas_necesarias = [
                'Se ocultan las columnas por seguridad pero aqui se ponen solo las columnas necesarias en este proceso'
            ]
            #jalamos la que en efecto existen
            cols_presentes = [c for c in columnas_necesarias if c in df_dict.columns]
            #Hacemos una normalizacion de los terminos y nos quedamos con los unicos
            self.df_dict = df_dict[cols_presentes].drop_duplicates(subset='termino representativo').copy()
            self.df_dict['termino representativo'] = self.df_dict['termino representativo'].str.lower().str.strip()
        else:
            self.df_dict = None

    #Se hace la primer funcion en la que vamos a extraer el dataframe de un google sheets, en esta funcion se obtiene el listado de skus
    #que se obtuvieron del calculo de performance
    def _listado_performance(self, df_performance, col_decision_final):
        '''
        Se va a hacer un concatenado de skus para cargar a nuestra plataforma por termino agrupador
        df_performance: Dataframe | Se debe ingestar el dataframe ordenado por termino agrupador y score al menos. Se necesitan al menos 
        columnas de validacion si el articulo es relevante o no al termino, el termino y un id que se utiliza de manera interna
        
        La funcion regresa un dataframe simplificado con concatenados de skus por termino agrupador y id para identificar su carga masiva
        '''
        #Se toman solo los skus validos por termino agrupador
        df_valido = df_performance[df_performance[col_decision_final] == 'Si'].copy()
        #Se hace limpieza sobre los datos
        df_valido = df_valido.drop_duplicates(subset=['termino_agg', 'sku']).sort_values(by=['termino_agg', 'col_de_poisicionamiento_de_skus'])
        df_valido['sku'] = df_valido['sku'].astype(str)
        #Se hace el concatenado por termino agrupador
        df_agrupado = df_valido.groupby('termino_agg').agg({'sku': ','.join, 'id': 'first'})
        #Se normaliza el índice para evitar cualquier problema
        df_agrupado.index = df_agrupado.index.astype(str).str.lower().str.strip()
        #Regresamos el dataFrame ya trabajado
        return df_agrupado.reset_index()

    def _obtener_penultima_semana(self, spreadsheet_name, cantidad_semanas):
        '''
        El objetivo principal de esta funcion es que de un google sheets en el que otros equipos vacian sus skus a impulsar,
        se maneja por ahora por pestañas de carga con sus validaciones internas (se espera poder migrar a una herramienta de versionado
        mas consistente como github), por lo que carga automatica por semana y a disposicion de ser necesario es importante y esta 
        funcion nos ayudara con ello
        spreadsheet_name: str   |  El nombre del google sheets para que python lo abra completo
        cantidad_semanas: int   |  Numero que se le restara al numero maximo de carga en la que nos encontremos 
        '''
        #Leemos el sheet y guardamos el nombre de sus hojas
        sh = self.client.open(spreadsheet_name)
        hojas = [ws.title for ws in sh.worksheets()]
        
        #Generamos la lista de los nombres con expresiones regulares que se va creando cada carga
        numeros_semana = []
        for nombre in hojas:
            match = re.search(r'Carga\s*(\d+)', nombre, re.IGNORECASE)
            if match: numeros_semana.append(int(match.group(1)))
        
        if not numeros_semana: return None
        #Obtenemos el maximo de carga para posteriormente quitarle el numero que se ingesta al llamar la funcion y metemos flags
        max_sem = max(numeros_semana)
        print(f'La última carga en el archivo de apuestas es: {max_sem}')
        semana_objetivo = max_sem - cantidad_semanas
        nombre_final = f"Carga {semana_objetivo}"
        #Regresamos el nombre de la hoja con la que se va a trabajar para poderlo llamar en otra funcion
        return nombre_final if nombre_final in hojas else None

    def _aux_especialistas(self, spreadsheet_name, cant_semanas):
        '''
        Esta funcion es la que va a hacer un preproceso antes de obtener los concatenados de skus como en performance pero 
        ahora del aarchivo de skus propuestos, las variables seran las mismas que en la funcion _obtener_penultima_semanaa
        '''
        if not self.client: return None
        #Mandamos a llamar a la funcion para jalar la carga de skus deseada
        nombre_hoja = self._obtener_penultima_semana(spreadsheet_name, cant_semanas)
        print(f'se va a trabajar con la hoja {nombre_hoja}')
        if not nombre_hoja: return None
        #abrimos el sheet y guardamos sus datos, ademas de que rellenamos los vacios de sheets con nan para aveitar problemas
        sh = self.client.open(spreadsheet_name).worksheet(nombre_hoja)
        data = sh.get('A:H')
        df = pd.DataFrame(data[1:], columns=data[0])
        df['SKU'] = df['SKU'].replace(r'^\s*$', np.nan, regex=True)
        #nos quedamos solo con los skus puestos y seteamos el formato que se nos pide quitandole las comas (que luego se les van)
        df = df.dropna(subset=['SKU', 'Term Representativo']).copy()
        df['SKU'] = df['SKU'].astype(str).str.strip().str.replace(',','')
        #Validamos que los skus vayan con el formato preestablecido con expresiones regulares
        patron_sku = r'^(PM-\d{3,}3|PR-\d{3,}2|MKP-\d{4,})$'
        #Hacemos el match de los patrones de skus
        df['es_valido'] = df['SKU'].str.match(patron_sku)

        #Filtramos los skus que no hayan cumplido con el formato preestablecido para poder darle atencion y corregirlos o descartarlos
        df_errores = df[df['es_valido'] == False]
        if not df_errores.empty:
            print("\n" + "!"*60)
            print("Se tienen skus con mal formato, y son:")
            # Agrupamos por termino para ver donde esta el error
            errores_detallados = df_errores.groupby('Term Representativo')['SKU'].apply(list).to_dict()
            for term, skus in errores_detallados.items():
                print(f"Término: {term} -> SKUs inválidos: {skus}")
            print("!"*60 + "\n")
            return None
        return df

    def _listados_especialistas(self, spreadsheet_name, cant_semanas):
        '''
        Aqui es donde se se hacen los concatenados de skus mandando a llamar a la funcion que hace los preprocesos, se mantienen
        las mismas variables que en _aux_especialistas y _obtener_penultima_semana
        '''
        #mandamos a llamar a la funcion de preproceso
        df = self._aux_especialistas(spreadsheet_name, cant_semanas)
        if df is None: return None
        #Generamos los concatenados de skus con los formatos establecidos en la plataforma de uso
        df_agrupado = df.groupby('Term Representativo').agg({'SKU': ','.join}).reset_index()
        #Normalizamos los terminos para despues poder hacer un cruce en otra funcion
        df_agrupado['Term Representativo'] = df_agrupado['Term Representativo'].str.lower().str.strip()
        #Regresamos el dataFrame de listados de skus propuestos
        return df_agrupado

    def mezclar_listados(self, row, col_a, col_b, take_a, take_b):
        '''
        Esta funcion es auxiliar para poder hacer el mix de skus de performance y de los propuestos directamente en una proporcion 
        preestablecida por termino agrupador (esta info se da en el diccionario), entonces se toman las dos columnas de concatenados
        el termino agrupador, los numeros en los que se hace el mix, si se va a tener un concatenado al inicio fijo sin que se haga mix
        y se tiene adicional la posibilidad de hacer mix de tipo marketplace vs propios en la proporcion indicada en el diccionario
        row : obj           | Simplemente es la fila en la que va a entrar la funcion cuando la vayamos a aplicar fila por fila del 
                              diccionario con los concatenados
        col_a : str         | Nombre de la columna en la que va el primer tipo de concatenados
        con_b : str         | Nombre de la columna en la que va el segundo tipo de concatenados
        take_a : int        | Numero en el que se va a hacer el mix principal para la col_a
        take_b : int        | Numero en el que se va a hacer el mix principal para la col_b
        list_especial : str | Nombre de la columna en la que se va a decir si se quiere o no mantener un listado fijo al inicio
        skus_esp : int      | Numero de espacios que se van a mantener fijos al inicio y que de igual manera se lee desde el archivo de apuestas
        '''
        #Se mete este if para poder hacer el mix en un par de ocasiones solo si el termino lo requiere

        try:
            #vamos a obtener el numero de mix para las cols a y b (los concatenados) evitando que se rompa si se nos paso el numero
            #dentro del archivo diccionario
            t_a = int(take_a) if pd.notnull(take_a) else 1
            t_b = int(take_b) if pd.notnull(take_b) else 1
        except:
            t_a, t_b = 2, 1
                
        #obtenemos los concatenados por fila y tipo apuesta o performance segun el caso
        val_a = str(row[col_a]) if pd.notnull(row[col_a]) else ""
        val_b = str(row[col_b]) if pd.notnull(row[col_b]) else ""
            
        #Generamos una lista abriendo los concatenados para poder hacer el mix (quiza podriamos lanzar antes la lista pero esto
        # nos ayuda en caso de que necesitemos poner o checar algun tipo de concatenado)
        lista_a = [s.strip() for s in val_a.split(',') if s.strip() and s.strip().lower() != 'nan']
        lista_b = [s.strip() for s in val_b.split(',') if s.strip() and s.strip().lower() != 'nan']
            
        #inicializamos las variables
        resultado, i, j = [], 0, 0

        #Empezamos a hacer el mix original y vamos entrando a cada lista de skus mantiendo el mix de t_a y t_b 
        #haciendo un append de cada sku
        while i < len(lista_a) or j < len(lista_b):
            for _ in range(t_a):
                if i < len(lista_a):
                    if lista_a[i] not in resultado: resultado.append(lista_a[i])
                    i += 1
            for _ in range(t_b):
                if j < len(lista_b):
                    if lista_b[j] not in resultado: resultado.append(lista_b[j])
                    j += 1
        #Finalmente regresamos el concatenado en la estructura que se nos pide por la plataforma
        return ",".join(resultado)
        

    def extraer_fijos(self, row):
      '''
      Esta funcion sirve para jalar los listados fijos en el archivo de apuestas (no se encarga del mix)
      Solo necesita la fila en la que va a operar para sacar el termino representativo, los valores de espacios fijos y la validacion de
      si se necestia o no
      '''
      #Se obtienen el termino y el si o no es listado especial y procedemos a entrar a trabajar cuando es si
      term_actual = str(row.get('termino representativo', 'N/A'))
      valor_especial = str(row.get('listado_especial', 'no'))
      
      if valor_especial == 'si':
          # Tomamos el valor ya limpio del paso anterior
          skus_esp = int(row.get('espacios_especiales', 0))
          val_b = str(row.get('SKU', "")) 
          
          # Si la columna SKU esta vacia, no podemos extraer nada
          if not val_b or val_b == "":
              print(f"[{term_actual}]: Dice si, pero NO HAY SKUs en la columna de apuestas (SKU).")
              return "", ""
          
          #En caso de que si haya sacamos los valores de los skus de apuestas en una lista
          lista_b = [s.strip() for s in val_b.split(',') if s.strip()]
          
          #Separamos la lista de skus en los que se van a quedar fijos y los que si se van a mixear
          fijos = lista_b[:skus_esp]
          restantes = lista_b[skus_esp:]
          
          print(f"[{term_actual}]: Se extraen {skus_esp} fijos. Encontrados: {len(fijos)}. (SKUs: {fijos})")
          return ",".join(fijos), ",".join(restantes)
              
      return "", str(row.get('SKU', ""))

    def cruce_de_listados(self, spreadsheet_apuestas, df_performance, col_decision_final, default_n_perf, default_n_espec, c_semanas):
      '''
      Esta funcion es la que se va a llamar en el main y que recopila todas las funciones antes hechas en el orden necesario
      spreadsheet_apuestas : str    | Nombre del sheets de apuestas
      df_performance : DataFrame    | Se ingesta el dataframe de performance que se obtuvo de manera externa
      col_decision_final : str      | Nombre de la columna del dataframe de performance en la que se dice si el sku es relevante al termino o no
      default_n_perf : int          | Valor de mix para el performance (es el mix principal por termino)
      default_n_espec : int         | Valor de mix para las apuestas (es el mix principal por termino)
      c_semanas : int               | Numero que se le va a restar al numero de carga maximo en el archivo de apuestas
      '''
      df_apuestas = self._listados_especialistas(spreadsheet_name=spreadsheet_apuestas, cant_semanas=c_semanas)
      df_perf_agrupado = self._listado_performance(df_performance, col_decision_final)
      
      if df_apuestas is None: return None

      # Merge inicial para poder tener toda la info en un solo datafraame
      df_completo = self.df_dict.merge(df_perf_agrupado, left_on='termino representativo', right_on='termino_agg', how='left')
      df_completo = df_completo.merge(df_apuestas, left_on='termino representativo', right_on='Term Representativo', how='left')

      # Aseguramos que 'Espacios_especiales' sea un numero y sin NaNs
      df_completo['espacios_especiales'] = pd.to_numeric(df_completo['espacios_especiales'], errors='coerce').fillna(0).astype(int)

      # Aseguramos que 'listado_especial' sea string y sin NaNs
      df_completo['listado_especial'] = df_completo['listado_especial'].astype(str).str.lower().str.strip().fillna('no')

      # Aseguramos que 'SKU' (el de apuestas) sea string
      df_completo['SKU'] = df_completo['SKU'].astype(str).replace(['nan', 'None', ''], '')

      # Creamos dos columnas: la que se queda fija y la que sobra para el cruce en caso de que el termino necesite skus fijos al inicio
      # Aplicamos y expandimos resultados a dos columnas nuevas
      df_res = df_completo.apply(self.extraer_fijos, axis=1, result_type='expand')
      df_completo['listado_fijo_inicial'] = df_res[0]
      df_completo['SKU_disponibles'] = df_res[1]

      # Ahora si hacemos el mix de los skus restantes en el proceso anterior con los de performance
      df_completo['listado_prefinal'] = df_completo.apply(
          lambda s: self.mezclar_listados(
              s, 'sku', 'SKU_disponibles', 
              s.get(self.col_n_perf, default_n_perf), 
              s.get(self.col_n_espec, default_n_espec)
          ), axis=1
      )
      
      # Separamos los concatenados en solo marketplace y propios
      df_completo['lista_pre_aux'] = df_completo['listado_prefinal'].astype(str).str.split(',')
      df_completo['skus_1p'] = df_completo['lista_pre_aux'].apply(
          lambda lista: ','.join(s.strip() for s in lista if s.strip().startswith(('PM', 'PR')))
      )
      df_completo['skus_3p'] = df_completo['lista_pre_aux'].apply(
          lambda lista: ','.join(s.strip() for s in lista if s.strip().startswith('MKP'))
      )
      
      #Hacemos la mezcla de los skus de marketplace y los propios
      df_completo['mezcla_dinamica'] = df_completo.apply(
          lambda s: self.mezclar_listados(
              s, col_a='skus_1p', col_b='skus_3p', 
              take_a=s.get('cruce_1p', 1), 
              take_b=s.get('cruce_3p', 1),
          ), axis=1
      )
      
      # Pegamos los fijos al principio de la mezcla
      def ensamblar_final(row):
          fijos = row['listado_fijo_inicial']
          mezcla = row['mezcla_dinamica']
          if fijos:
              # Si hay mezcla, los unimos; si no, solo quedan los fijos
              return f"{fijos},{mezcla}".strip(',') if mezcla else fijos
          return mezcla

      df_completo['listado_final'] = df_completo.apply(ensamblar_final, axis=1)
      
      return df_completo[df_completo['listado_final'] != ""].copy()