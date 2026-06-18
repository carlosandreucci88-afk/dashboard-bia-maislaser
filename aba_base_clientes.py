# ============================================================
# 📊 ABA "BASE DE CLIENTES" — Fase 7, Etapa 1
# ============================================================
# COMO INTEGRAR NO dashboard_maislaser.py:
#
# 1) Cole TODO este bloco antes do `st.tabs(...)` principal do dashboard
#
# 2) Adicione a nova tab na chamada do tabs:
#    Exemplo: se hoje você tem
#        tab_conv, tab_transf, tab_agend, tab_metr, tab_conf = st.tabs(
#            ["💬 Conversas", "🔥 Transferências", "📅 Agendamentos",
#             "📊 Métricas", "⚙️ Configurações"]
#        )
#    Mude para:
#        tab_conv, tab_transf, tab_agend, tab_base, tab_metr, tab_conf = st.tabs(
#            ["💬 Conversas", "🔥 Transferências", "📅 Agendamentos",
#             "📊 Base de Clientes", "📊 Métricas", "⚙️ Configurações"]
#        )
#
# 3) E onde for renderizar a aba:
#    with tab_base:
#        render_aba_base_clientes(supabase)
#
# REQUISITOS:
#   pip install pandas openpyxl
#   (supabase, streamlit e datetime já devem estar no requirements)
#
# CHANGELOG:
#   v1.1 (18/06/2026): Fix bug "Resumo mostra só 1.000 linhas"
#                      Adicionado helper _fetch_all_paginado() que pagina
#                      automaticamente via .range() em batches de 1000 até
#                      retornar tudo. Aplicado em _render_resumo_base
#                      (conta TODOS os 50k) e _render_visualizar_clientes
#                      (suporte a até 5000 resultados de filtro).
# ============================================================

import io
import re
import json
from datetime import datetime, date
from typing import Any

import pandas as pd
import streamlit as st


# ============================================================
# CONSTANTES
# ============================================================

TELEFONE_CARLOS = '5511976473948'

COLUNAS_ESPERADAS_XLSX = [
    'Data', 'Cliente', 'Celular', 'Data de nascimento', 'Gênero',
    'Última compra', 'Último agendamento realizado', 'Número',
]

UNIDADES_VALIDAS = ['Mogi das Cruzes', 'Suzano']

CATEGORIAS_NOME = {
    'BLOQUEIO': [
        r'\bmenor\b', r'\bcriança\b', r'\bteste\b', r'\bfake\b', r'\bfalso\b',
        r'\bdemo\b', r'franqueador', r'treinamento', r'duplicad', r'cancelad',
        r'\bbanid', r'bloquead',
    ],
    'PII': [
        r'\bgrávid', r'\bgravid', r'\bdiabetes\b', r'\bcâncer\b', r'\bcancer\b',
        r'\bquimio', r'\bdepress', r'\bansiedade\b',
    ],
    'REVISAR': [
        r'\binadimplente\b', r'\bdevedor\b', r'problemátic', r'reclamona',
        r'\bchato\b', r'\bdifícil\b', r'grosseir',
    ],
    'RELACAO': [
        r'mãe da\b', r'mae da\b', r'irmã da\b', r'irma da\b',
        r'esposa do\b', r'filha da\b', r'amiga da\b', r'\besposo\b',
    ],
}

PALAVRAS_LIMPAVEIS = [
    r'\bconfirmar\b', r'\bverificar\b', r'\bchecar\b',
    r'\bobs\b', r'\bnota\b', r'\batenção\b', r'\balerta\b',
    r'\bpl\b', r'\bfinan', r'\baniversariante\b', r'\baniversário\b',
]


# ============================================================
# HELPER — PAGINAÇÃO AUTOMÁTICA (v1.1)
# ============================================================
# O cliente Python do Supabase tem um LIMITE PADRÃO de 1000 linhas por
# response (mesmo sem .limit() explícito). Pra tabelas grandes (50k+) é
# necessário paginar manualmente via .range(start, end).
#
# Este helper faz isso transparentemente: roda em loop até pegar tudo.

def _fetch_all_paginado(query_builder_factory, page_size: int = 1000,
                        max_pages: int = 100) -> list:
    """
    Pagina uma query do Supabase em batches de `page_size`.

    Args:
        query_builder_factory: callable que retorna uma query FRESCA
                                (não reutilizável). Necessário porque
                                cada call a .range() consome o builder.
        page_size: tamanho de cada página (max 1000 no Supabase).
        max_pages: limite de segurança (100k linhas no default).

    Returns:
        Lista plana de dicts (todos os resultados concatenados).

    Exemplo de uso:
        def make_query():
            q = sb.table('clientes_base').select('tipo, unidade')
            if filtro:
                q = q.eq('unidade', filtro)
            return q

        registros = _fetch_all_paginado(make_query, page_size=1000)
    """
    todos = []
    for pagina in range(max_pages):
        start = pagina * page_size
        end = start + page_size - 1  # range é INCLUSIVO no Supabase
        try:
            query = query_builder_factory().range(start, end)
            res = query.execute()
        except Exception as e:
            # Aborta paginação em caso de erro mas mantém o que já pegou
            st.warning(f'⚠️ Paginação interrompida na página {pagina + 1}: {str(e)[:200]}')
            break

        batch = res.data or []
        todos.extend(batch)

        # Última página: veio menos que o page_size (ou veio zero)
        if len(batch) < page_size:
            break
    else:
        # Atingiu max_pages sem parar — aviso defensivo
        st.warning(
            f'⚠️ Atingido limite de {max_pages} páginas ({max_pages * page_size} linhas). '
            f'Pode haver dados não exibidos. Aumente `max_pages` se necessário.'
        )

    return todos


# ============================================================
# CAMADA 1 — VALIDAÇÃO TÉCNICA DE TELEFONE
# ============================================================

def normalizar_telefone(valor: Any) -> str:
    if pd.isna(valor):
        return ''
    s = str(int(valor)) if isinstance(valor, float) else str(valor)
    return re.sub(r'\D', '', s)


def validar_telefone(t: str) -> tuple[bool, str]:
    if not t:
        return False, 'vazio'
    if len(t) != 13:
        return False, f'tamanho_{len(t)}_digitos'
    if not t.startswith('55'):
        return False, 'sem_prefixo_55'
    if t[4] != '9':
        return False, 'quinto_digito_nao_9'
    return True, 'ok'


# ============================================================
# CAMADA 2 — LIMPEZA DE NOME (regex)
# ============================================================

def limpar_nome(nome_original: Any) -> str:
    if not isinstance(nome_original, str):
        return ''
    n = nome_original
    n = re.sub(r'\([^)]*\)', '', n)
    n = re.sub(r'\b\d{3}\.\d{3}\.\d{3}-?\d{2}\b', '', n)
    n = re.sub(r'\d{8,}', '', n)
    n = re.sub(r'\(?\d{2}\)?\s*\d{4,5}-?\d{4}', '', n)
    n = re.sub(r'\d{1,2}/\d{1,2}/\d{2,4}', '', n)
    n = re.sub(r'\b\d+\b', '', n)
    n = re.sub(r'\bpl\b[:,.\s-]*', '', n, flags=re.IGNORECASE)
    n = re.sub(r'^[\s\-,.;:|]+|[\s\-,.;:|]+$', '', n)
    n = re.sub(r'\s+', ' ', n).strip()

    if n:
        preposicoes = {'da', 'de', 'do', 'das', 'dos', 'e'}
        palavras = n.lower().split()
        n = ' '.join(
            w if (i > 0 and w in preposicoes) else w.capitalize()
            for i, w in enumerate(palavras)
        )
    return n


# ============================================================
# CAMADA 3 — CATEGORIZAÇÃO SEMÂNTICA
# ============================================================

def categorizar_nome(nome_original: Any, nome_limpo: str) -> tuple[str, list]:
    """
    Retorna (categoria, palavras_chave_detectadas).
    Categorias: LIMPO, BLOQUEIO, LIMPADO, REVISAR, PII, RELACAO
    """
    if not isinstance(nome_original, str):
        return 'BLOQUEIO', ['nome_vazio']

    n = nome_original.lower()
    palavras = []

    for cat in ['BLOQUEIO', 'PII', 'REVISAR', 'RELACAO']:
        for padrao in CATEGORIAS_NOME[cat]:
            if re.search(padrao, n):
                palavras.append(padrao.strip(r'\b'))
                return cat, palavras

    for padrao in PALAVRAS_LIMPAVEIS:
        if re.search(padrao, n):
            palavras.append(padrao.strip(r'\b'))
            if nome_limpo and len(nome_limpo) >= 2:
                return 'LIMPADO', palavras
            return 'REVISAR', palavras

    return 'LIMPO', []


# ============================================================
# CLASSIFICAÇÃO DOS 6 TIPOS
# ============================================================

def classificar_tipo(row, hoje):
    tem_compra = pd.notna(row['ultima_compra'])
    tem_agend = pd.notna(row['ultimo_agendamento'])
    cad = row['data_cadastro']
    dias_cadastro = (hoje - cad).days if pd.notna(cad) else 9999

    if tem_compra and tem_agend:
        d1 = (hoje - row['ultima_compra']).days
        d2 = (hoje - row['ultimo_agendamento']).days
        return '1' if min(d1, d2) <= 180 else '2'
    if not tem_compra and tem_agend:
        return '3A' if dias_cadastro <= 365 else '3B'
    if tem_compra and not tem_agend:
        return '4'
    return '5'


# ============================================================
# ANÁLISE DA PLANILHA (sem gravar)
# ============================================================

def analisar_planilha(file_bytes: bytes, unidade: str, hoje: datetime) -> dict:
    """
    Lê o XLSX, aplica todas as 3 camadas + classificação, retorna dict com:
      - df_validos    : DataFrame pronto pra inserir
      - df_invalidos  : DataFrame dos rejeitados
      - stats         : contagens agregadas
      - carlos_na_base: bool
      - colunas_ok    : bool
      - colunas_recebidas: list
    """
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name='Clientes')
    colunas_recebidas = list(df.columns)
    colunas_ok = colunas_recebidas == COLUNAS_ESPERADAS_XLSX

    df.columns = [
        'data_cadastro', 'nome_original', 'celular', 'data_nascimento',
        'genero', 'ultima_compra', 'ultimo_agendamento', 'numero',
    ]
    total_brutas = len(df)

    df['telefone'] = df['celular'].apply(normalizar_telefone)
    df = df.sort_values('data_cadastro', ascending=False).drop_duplicates(
        subset=['telefone'], keep='first'
    )
    total_unicos = len(df)

    df['valido'], df['motivo_invalido'] = zip(*df['telefone'].apply(validar_telefone))
    df_validos = df[df['valido']].copy()
    df_invalidos = df[~df['valido']].copy()

    df_validos['nome_limpo'] = df_validos['nome_original'].apply(limpar_nome)
    cats = df_validos.apply(
        lambda r: categorizar_nome(r['nome_original'], r['nome_limpo']),
        axis=1
    )
    df_validos['categoria_nome'] = [c[0] for c in cats]
    df_validos['palavras_detectadas'] = [c[1] for c in cats]

    sem_nome = df_validos['nome_limpo'].str.len().fillna(0) < 2
    df_validos.loc[sem_nome, 'categoria_nome'] = 'BLOQUEIO'

    df_validos['tipo'] = df_validos.apply(
        lambda r: classificar_tipo(r, hoje), axis=1
    )
    df_validos['compra_dias'] = df_validos['ultima_compra'].apply(
        lambda d: (hoje - d).days if pd.notna(d) else None
    )

    stats = {
        'total_brutas': total_brutas,
        'total_unicos': total_unicos,
        'validos': len(df_validos),
        'invalidos': len(df_invalidos),
        'por_tipo': df_validos['tipo'].value_counts().to_dict(),
        'por_categoria': df_validos['categoria_nome'].value_counts().to_dict(),
        'por_motivo_invalido': df_invalidos['motivo_invalido'].value_counts().to_dict(),
    }

    return {
        'df_validos': df_validos,
        'df_invalidos': df_invalidos,
        'stats': stats,
        'carlos_na_base': (df_validos['telefone'] == TELEFONE_CARLOS).any(),
        'colunas_ok': colunas_ok,
        'colunas_recebidas': colunas_recebidas,
        'unidade': unidade,
    }


# ============================================================
# CONVERSÃO PRA REGISTROS SUPABASE
# ============================================================

def _date_iso(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (datetime, date)):
        return v.strftime('%Y-%m-%d')
    return None


def _str_or_none(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == '':
        return None
    return str(v)


def df_validos_para_registros(df_validos: pd.DataFrame, unidade: str) -> list:
    registros = []
    for _, r in df_validos.iterrows():
        meta = {
            'palavras_chave': r['palavras_detectadas'],
            'nome_foi_limpo': r['categoria_nome'] == 'LIMPADO',
        }
        if r['tipo'] == '4' and r['compra_dias'] is not None and r['compra_dias'] <= 30:
            meta['tipo4_recente'] = True
        registros.append({
            'telefone': r['telefone'],
            'nome': r['nome_limpo'],
            'nome_original': _str_or_none(r['nome_original']),
            'genero': _str_or_none(r['genero']),
            'data_nascimento': _date_iso(r['data_nascimento']),
            'ultima_compra': _date_iso(r['ultima_compra']),
            'ultimo_agendamento': _date_iso(r['ultimo_agendamento']),
            'unidade': unidade,
            'data_cadastro': _date_iso(r['data_cadastro']),
            'tipo': r['tipo'],
            'categoria_nome': r['categoria_nome'],
            'metadados': meta,
        })
    return registros


def df_invalidos_para_registros(df_invalidos: pd.DataFrame) -> list:
    return [
        {
            'telefone': r['telefone'] or None,
            'nome_original': _str_or_none(r['nome_original']),
            'motivo': r['motivo_invalido'],
        }
        for _, r in df_invalidos.iterrows()
    ]


# ============================================================
# IMPORTAÇÃO PARA O SUPABASE (em batches via API)
# ============================================================

def importar_para_supabase(
    supabase, df_validos, df_invalidos, unidade,
    batch_size=100, progress_callback=None
) -> dict:
    """
    Insere em batches via API do Supabase.
    Usa upsert (on_conflict='telefone') no clientes_base — permite reimport.
    progress_callback(atual, total, mensagem) chamado a cada batch.
    """
    erros = []
    inseridos_base = 0
    inseridos_invalidos = 0

    registros_validos = df_validos_para_registros(df_validos, unidade)
    registros_invalidos = df_invalidos_para_registros(df_invalidos)

    total_batches = (
        (len(registros_validos) + batch_size - 1) // batch_size
        + (len(registros_invalidos) + batch_size - 1) // batch_size
    )
    batch_atual = 0

    # ---- clientes_base (upsert) ----
    for i in range(0, len(registros_validos), batch_size):
        batch = registros_validos[i:i + batch_size]
        batch_atual += 1
        if progress_callback:
            progress_callback(
                batch_atual, total_batches,
                f'Importando clientes válidos: {i + len(batch)}/{len(registros_validos)}'
            )
        try:
            supabase.table('clientes_base').upsert(
                batch, on_conflict='telefone'
            ).execute()
            inseridos_base += len(batch)
        except Exception as e:
            erros.append(f'Batch válidos {i}-{i+len(batch)}: {str(e)[:200]}')

    # ---- clientes_invalidos (insert puro) ----
    for i in range(0, len(registros_invalidos), batch_size):
        batch = registros_invalidos[i:i + batch_size]
        batch_atual += 1
        if progress_callback:
            progress_callback(
                batch_atual, total_batches,
                f'Importando inválidos: {i + len(batch)}/{len(registros_invalidos)}'
            )
        try:
            supabase.table('clientes_invalidos').insert(batch).execute()
            inseridos_invalidos += len(batch)
        except Exception as e:
            erros.append(f'Batch inválidos {i}-{i+len(batch)}: {str(e)[:200]}')

    # ---- Carlos no lista_ignorar_teste ----
    if (df_validos['telefone'] == TELEFONE_CARLOS).any():
        try:
            supabase.table('lista_ignorar_teste').upsert(
                {'telefone': TELEFONE_CARLOS,
                 'motivo': 'Telefone de teste do Carlos (também na base)'},
                on_conflict='telefone'
            ).execute()
        except Exception as e:
            erros.append(f'lista_ignorar_teste: {str(e)[:200]}')

    return {
        'inseridos_base': inseridos_base,
        'inseridos_invalidos': inseridos_invalidos,
        'erros': erros,
    }


# ============================================================
# UI — RENDER DA ABA
# ============================================================

TIPO_LABELS = {
    '1': '🟢 1. Pacote Ativa',
    '2': '🔴 2. Pacote Dormente',
    '3A': '🟡 3A. Voucher Válido',
    '3B': '🟡 3B. Voucher Expirado',
    '4': '🟠 4. Pagou Não Veio',
    '5': '⚪ 5. Nunca Veio',
}

CATEGORIA_LABELS = {
    'LIMPO': '✅ Limpo (sem anotação)',
    'LIMPADO': '🟡 Limpado (anotação removida, OK pra disparar)',
    'BLOQUEIO': '🔴 Bloqueio (teste, menor, NÃO disparar)',
    'PII': '🔵 PII médica (LGPD, NÃO disparar)',
    'REVISAR': '🟠 Revisar manualmente',
    'RELACAO': '⚪ Relação (cadastro de terceiro)',
}


def _render_resumo_base(supabase):
    """Sub-aba: resumo do que já está importado no banco.

    FIX v1.1: Antes usava `query.execute()` direto, que retornava no MÁXIMO
    1000 linhas (limite padrão do Supabase Python client). Pra base com
    50k+ clientes, mostrava 'Total: 1000' incorretamente. Agora pagina
    automaticamente via _fetch_all_paginado() — pega TUDO.
    """
    st.subheader('📊 Resumo da base importada')

    col_unid, _ = st.columns([2, 3])
    with col_unid:
        filtro_unidade = st.selectbox(
            'Unidade', ['Todas'] + UNIDADES_VALIDAS, key='base_filtro_unid'
        )

    # ─── Paginação automática (v1.1) ──────────────────────────
    # Como Supabase tem limite 1000/response, paginamos em loop até pegar tudo.
    # Função factory cria query FRESCA a cada página (range() consome builder).
    def _query_resumo():
        q = supabase.table('clientes_base').select('tipo, categoria_nome, unidade')
        if filtro_unidade != 'Todas':
            q = q.eq('unidade', filtro_unidade)
        return q

    with st.spinner('Carregando resumo da base completa...'):
        registros = _fetch_all_paginado(_query_resumo, page_size=1000, max_pages=100)

    df = pd.DataFrame(registros)

    if df.empty:
        st.info('Nenhum cliente na base ainda. Use a aba "📥 Importar" pra começar.')
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Total na base', f'{len(df):,}'.replace(',', '.'))
    c2.metric('Mogi', int((df['unidade'] == 'Mogi das Cruzes').sum()))
    c3.metric('Suzano', int((df['unidade'] == 'Suzano').sum()))
    bloqueados = int((df['categoria_nome'] == 'BLOQUEIO').sum())
    c4.metric('Bloqueados', bloqueados)

    st.markdown('**Distribuição por tipo:**')
    por_tipo = df['tipo'].value_counts().to_dict()
    total = len(df)
    linhas_tipo = []
    for t, label in TIPO_LABELS.items():
        n = por_tipo.get(t, 0)
        pct = 100 * n / total if total else 0
        linhas_tipo.append({'Tipo': label, 'Quantidade': n, '%': f'{pct:.1f}%'})
    st.dataframe(pd.DataFrame(linhas_tipo), hide_index=True, width='stretch')

    st.markdown('**Categoria de nome:**')
    por_cat = df['categoria_nome'].value_counts().to_dict()
    linhas_cat = [
        {'Categoria': CATEGORIA_LABELS.get(c, c), 'Quantidade': n}
        for c, n in sorted(por_cat.items(), key=lambda x: -x[1])
    ]
    st.dataframe(pd.DataFrame(linhas_cat), hide_index=True, width='stretch')


def _render_visualizar_clientes(supabase):
    """Sub-aba: navegar nos clientes importados com filtros.

    FIX v1.1: Antes tinha .limit(500) hardcoded e response truncado em 1000.
    Agora pagina até max_pages=5 (5000 resultados — suficiente pra navegação
    com filtros). Pra busca específica, recomenda usar campo 'Buscar' que
    filtra no banco e retorna pouco.
    """
    st.subheader('🔍 Navegar pelos clientes')

    c1, c2, c3, c4 = st.columns(4)
    f_unid = c1.selectbox('Unidade', ['Todas'] + UNIDADES_VALIDAS, key='nav_unid')
    f_tipo = c2.selectbox(
        'Tipo', ['Todos'] + list(TIPO_LABELS.keys()), key='nav_tipo'
    )
    f_cat = c3.selectbox(
        'Categoria', ['Todas'] + list(CATEGORIA_LABELS.keys()), key='nav_cat'
    )
    f_busca = c4.text_input('Buscar nome/telefone', '', key='nav_busca')

    # ─── Paginação automática (v1.1) ──────────────────────────
    def _query_navegar():
        q = supabase.table('clientes_base').select(
            'telefone, nome, nome_original, genero, tipo, categoria_nome, '
            'unidade, data_cadastro, ultima_compra, ultimo_agendamento'
        )
        if f_unid != 'Todas':
            q = q.eq('unidade', f_unid)
        if f_tipo != 'Todos':
            q = q.eq('tipo', f_tipo)
        if f_cat != 'Todas':
            q = q.eq('categoria_nome', f_cat)
        if f_busca:
            q = q.or_(f'nome.ilike.%{f_busca}%,telefone.ilike.%{f_busca}%')
        return q

    # Limita a 5000 resultados pra não travar o browser com tabela enorme
    with st.spinner('Carregando clientes...'):
        registros = _fetch_all_paginado(_query_navegar, page_size=1000, max_pages=5)

    df = pd.DataFrame(registros)

    if df.empty:
        st.info('Nenhum cliente encontrado com esses filtros.')
        return

    # Aviso se atingiu o limite (mostra que pode ter mais)
    if len(df) >= 5000:
        st.warning(
            f'⚠️ Mostrando os primeiros 5.000 resultados. '
            f'Use os filtros (unidade, tipo, categoria, busca) pra refinar.'
        )
    else:
        st.caption(f'📊 {len(df):,} cliente(s) encontrado(s)'.replace(',', '.'))

    st.dataframe(df, hide_index=True, width='stretch')


def _render_importar(supabase):
    """Sub-aba: upload + análise + importação."""
    st.subheader('📥 Importar nova planilha')
    st.caption(
        'Aceita o XLSX exportado do sistema interno (mesmas colunas: '
        'Data, Cliente, Celular, Data de nascimento, Gênero, Última compra, '
        'Último agendamento realizado, Número).'
    )

    c1, c2 = st.columns([2, 1])
    with c1:
        arquivo = st.file_uploader('Planilha XLSX', type=['xlsx'], key='base_upload')
    with c2:
        unidade = st.selectbox('Unidade', UNIDADES_VALIDAS, key='base_unidade')

    if not arquivo:
        st.info('Faça upload de uma planilha pra começar.')
        return

    # Cache da análise no session_state (evita reprocessar a cada interação)
    cache_key = f'analise_{arquivo.name}_{unidade}_{arquivo.size}'
    if cache_key not in st.session_state:
        with st.spinner('Analisando planilha (validação, limpeza, classificação)...'):
            try:
                resultado = analisar_planilha(
                    arquivo.getvalue(), unidade, datetime.now()
                )
                st.session_state[cache_key] = resultado
            except Exception as e:
                st.error(f'Erro ao analisar planilha: {e}')
                return

    r = st.session_state[cache_key]
    stats = r['stats']

    if not r['colunas_ok']:
        st.warning(
            f'⚠️ Colunas diferentes do esperado!\n\n'
            f'Esperado: {COLUNAS_ESPERADAS_XLSX}\n\n'
            f'Recebido: {r["colunas_recebidas"]}'
        )

    # ---- Resumo ----
    st.markdown('### 🔎 Análise prévia (nada foi gravado ainda)')
    m1, m2, m3, m4 = st.columns(4)
    m1.metric('Linhas brutas', f'{stats["total_brutas"]:,}'.replace(',', '.'))
    m2.metric('Únicos por telefone', f'{stats["total_unicos"]:,}'.replace(',', '.'))
    m3.metric('✅ Válidos', f'{stats["validos"]:,}'.replace(',', '.'),
              delta=f'{100 * stats["validos"] / max(stats["total_unicos"], 1):.1f}%')
    m4.metric('❌ Inválidos', f'{stats["invalidos"]:,}'.replace(',', '.'))

    if r['carlos_na_base']:
        st.info(
            '👀 O telefone do Carlos foi detectado na base. '
            'Ele será adicionado ao `lista_ignorar_teste` automaticamente '
            'durante a importação (mas continua no `clientes_base` pra auditoria).'
        )

    # ---- Tipos ----
    st.markdown('**Distribuição dos 6 tipos:**')
    linhas_tipo = []
    for t, label in TIPO_LABELS.items():
        n = stats['por_tipo'].get(t, 0)
        pct = 100 * n / stats['validos'] if stats['validos'] else 0
        linhas_tipo.append({'Tipo': label, 'Quantidade': n, '%': f'{pct:.1f}%'})
    st.dataframe(pd.DataFrame(linhas_tipo), hide_index=True, width='stretch')

    # ---- Categorias ----
    st.markdown('**Categoria de nome:**')
    linhas_cat = []
    for cat, label in CATEGORIA_LABELS.items():
        n = stats['por_categoria'].get(cat, 0)
        if n > 0:
            linhas_cat.append({'Categoria': label, 'Quantidade': n})
    st.dataframe(pd.DataFrame(linhas_cat), hide_index=True, width='stretch')

    # ---- Rejeitados ----
    n_rej = stats['invalidos'] + stats['por_categoria'].get('BLOQUEIO', 0)
    with st.expander(f'⚠️ Ver rejeitados ({n_rej} casos: inválidos + bloqueados)'):
        st.markdown('**Telefones inválidos (não vão pro `clientes_base`):**')
        if not r['df_invalidos'].empty:
            st.dataframe(
                r['df_invalidos'][['telefone', 'nome_original', 'motivo_invalido']],
                hide_index=True, width='stretch'
            )
        else:
            st.caption('Nenhum.')

        st.markdown('**Nomes bloqueados (vão pro `clientes_base` mas marcados):**')
        bloqueados = r['df_validos'][r['df_validos']['categoria_nome'] == 'BLOQUEIO']
        if not bloqueados.empty:
            st.dataframe(
                bloqueados[['telefone', 'nome_limpo', 'nome_original',
                            'palavras_detectadas']],
                hide_index=True, width='stretch'
            )
        else:
            st.caption('Nenhum.')

    # ---- BOTÃO IMPORTAR ----
    st.divider()
    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.markdown(
            f'### Pronto pra importar {stats["validos"]:,} clientes válidos + '
            f'{stats["invalidos"]} inválidos pra auditoria?'.replace(',', '.')
        )
        st.caption(
            f'Vai usar `upsert(on_conflict="telefone")` — se o cliente já existir, '
            f'atualiza os dados. Operação segura, pode rodar várias vezes.'
        )
    with col_b:
        confirmar = st.button(
            f'🚀 Importar agora', type='primary', width='stretch',
            key='btn_importar_base'
        )

    if confirmar:
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        def callback(atual, total, msg):
            progress_bar.progress(atual / total if total else 1.0)
            status_text.text(f'[{atual}/{total}] {msg}')

        with st.spinner('Importando...'):
            resultado = importar_para_supabase(
                supabase,
                r['df_validos'],
                r['df_invalidos'],
                unidade,
                batch_size=100,
                progress_callback=callback,
            )

        progress_bar.progress(1.0)
        status_text.empty()

        st.success(
            f'✅ Importação concluída!\n\n'
            f'• `clientes_base`: {resultado["inseridos_base"]:,} registros\n'
            f'• `clientes_invalidos`: {resultado["inseridos_invalidos"]:,} registros'
            .replace(',', '.')
        )

        if resultado['erros']:
            st.error(
                f'⚠️ Ocorreram {len(resultado["erros"])} erro(s) em batches. '
                f'Os outros batches foram inseridos normalmente.'
            )
            with st.expander('Ver detalhes dos erros'):
                for e in resultado['erros']:
                    st.code(e)

        # Limpa cache pra refletir o estado atualizado
        del st.session_state[cache_key]


def render_aba_base_clientes(supabase):
    """Função principal — chame dentro do `with tab_base:` no dashboard."""
    st.header('📊 Base de Clientes')
    st.caption(
        'Importa as planilhas das unidades e gerencia o reconhecimento de '
        'clientes existentes pela Bia. **Sem esta base, a Bia trata todo mundo '
        'como lead novo.**'
    )

    sub_resumo, sub_importar, sub_navegar = st.tabs([
        '📊 Resumo', '📥 Importar planilha', '🔍 Navegar clientes'
    ])

    with sub_resumo:
        _render_resumo_base(supabase)
    with sub_importar:
        _render_importar(supabase)
    with sub_navegar:
        _render_visualizar_clientes(supabase)

    # ═══════════════════════════════════════════════════════════
    # 📋 LEGENDA DOS TIPOS DE CLIENTE — rodapé da aba Base de Clientes
    # ═══════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown(
        """
        <div style="color: #8a8a8a; font-size: 0.78rem; line-height: 1.6; margin-top: 1.5rem; padding: 0.8rem 1rem; background: rgba(128,128,128,0.05); border-radius: 6px; border-left: 3px solid #d0d0d0;">
        <strong style="color: #6a6a6a;">📋 LEGENDA — Os 6 tipos da base oficial Maislaser</strong><br><br>
        <strong style="color: #5cb85c;">Tipo 1 — Pacote Ativa 🟢</strong> &nbsp;·&nbsp; Cliente em tratamento agora, comprou pacote e está fazendo as sessões. <em>Status comercial: RELACIONAMENTO</em><br><br>
        <strong style="color: #f0ad4e;">Tipo 2 — Pacote Dormente 🟡</strong> &nbsp;·&nbsp; Terminou pacote, não voltou pra mais. <em>Status comercial: RECONQUISTA</em><br><br>
        <strong style="color: #9b59b6;">Tipo 3A — Voucher Válido ✨</strong> &nbsp;·&nbsp; Ganhou cortesia (5 sessões grátis) e ainda NÃO usou — voucher ativo. <em>Status comercial: ATIVAÇÃO</em><br><br>
        <strong style="color: #e74c3c;">Tipo 3B — Voucher Expirado 💔</strong> &nbsp;·&nbsp; Ganhou cortesia mas o voucher expirou sem ser usado. <em>Status comercial: RECUPERAÇÃO</em><br><br>
        <strong style="color: #d9534f;">Tipo 4 — Pagou Não Veio / Encerrado / Cancelado 💸</strong> &nbsp;·&nbsp; Comprou algo mas não compareceu, contrato encerrado, ou cancelado. <em>Status comercial: RESOLUÇÃO DE PENDÊNCIA</em><br><br>
        <strong style="color: #95a5a6;">Tipo 5 — Nunca Veio ⚪</strong> &nbsp;·&nbsp; Cadastrado mas sem histórico de visita (lead frio importado). <em>Status comercial: CAPTAÇÃO</em>
        </div>
        """,
        unsafe_allow_html=True
    )
