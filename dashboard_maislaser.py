"""
==============================================================================
DASHBOARD MAISLASER — Bia
==============================================================================
Painel de monitoramento das conversas do robô Bia em tempo real.

Setup:
1. pip install -r requirements.txt
2. Configurar .streamlit/secrets.toml com:
       SUPABASE_URL = "https://pmorwdbmzbeaakutxhdk.supabase.co"
       SUPABASE_KEY = "sb_secret_..."
       DASHBOARD_PASSWORD = "sua_senha_aqui"
3. streamlit run dashboard_maislaser.py

Para deploy:
- Subir no Streamlit Cloud (gratuito): https://streamlit.io/cloud
- Conectar com este arquivo no GitHub
- Configurar os secrets no painel do Streamlit Cloud
==============================================================================
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import time
import hashlib

# ============================================================================
# CONFIGURAÇÃO INICIAL
# ============================================================================

st.set_page_config(
    page_title="Bia · Dashboard MaisLaser",
    page_icon="💚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Fuso de São Paulo (UTC-3)
TZ_SP = timezone(timedelta(hours=-3))

# Cor primária Maislaser
COR_PRIMARIA = "#22c55e"

# Custo aproximado do modelo Haiku 4.5 (USD por 1M tokens)
# Não temos separação input/output na tabela, então usamos uma média
CUSTO_USD_POR_MTOK = 3.0  # média entre input ($1) e output ($5)


# ============================================================================
# CSS — visual polido tipo WhatsApp
# ============================================================================

st.markdown("""
<style>
    .stApp { background-color: #f7f9fc; }
    
    .msg-cliente {
        background: #dcfce7;
        padding: 10px 14px;
        border-radius: 12px 12px 12px 2px;
        margin: 6px 0;
        max-width: 75%;
        margin-right: auto;
        word-wrap: break-word;
        white-space: pre-wrap;
    }
    .msg-bia {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        padding: 10px 14px;
        border-radius: 12px 12px 2px 12px;
        margin: 6px 0 6px auto;
        max-width: 75%;
        word-wrap: break-word;
        white-space: pre-wrap;
    }
    .msg-timestamp {
        font-size: 11px;
        color: #6b7280;
        margin-top: 2px;
    }
    .badge-alerta {
        background: #fee2e2;
        color: #991b1b;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
    }
    .badge-ok {
        background: #dcfce7;
        color: #166534;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
    }
    .badge-info {
        background: #dbeafe;
        color: #1e40af;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
    }
    .card-metric {
        background: white;
        padding: 16px;
        border-radius: 12px;
        border: 1px solid #e5e7eb;
        text-align: center;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 8px 20px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# AUTENTICAÇÃO SIMPLES
# ============================================================================

def _expected_login_token():
    """Token determinístico baseado na senha + sal fixo.
    Não revela a senha mesmo se vazar (é hash truncado)."""
    pw = st.secrets.get("DASHBOARD_PASSWORD", "maislaser")
    salt = "bia_maislaser_v6_persistencia"
    return hashlib.sha256((pw + salt).encode()).hexdigest()[:32]


def check_password():
    """Tela de login centralizada com 'Lembrar de mim' (persistente via URL).
    
    Como funciona o 'Lembrar de mim':
    - Ao logar com a opção marcada, um token (hash da senha) é colocado
      na URL como ?t=...
    - Em acessos futuros (mesma URL/bookmark), o dashboard reconhece o
      token e loga automaticamente, mesmo após dias.
    - Ao clicar Sair, o token é removido da URL.
    """
    
    # 1) Tenta auto-login via query string
    qp = st.query_params
    if "t" in qp and qp.get("t") == _expected_login_token():
        st.session_state["password_correct"] = True
    
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    
    if st.session_state["password_correct"]:
        return True
    
    # 2) CSS — esconde chrome do Streamlit e centraliza a caixa de login
    st.markdown("""
        <style>
            header[data-testid="stHeader"] {visibility: hidden;}
            section[data-testid="stSidebar"] {display: none;}
            section.main > div.block-container {
                padding-top: 2rem !important;
                max-width: 100% !important;
            }
            .bia-login-box {
                background: white;
                padding: 44px 40px;
                border-radius: 20px;
                box-shadow: 0 12px 40px rgba(34, 197, 94, 0.15);
                max-width: 440px;
                margin: 4vh auto 0 auto;
                text-align: center;
                border: 1px solid #e5e7eb;
            }
            .bia-login-title {
                font-size: 40px;
                font-weight: 700;
                color: #166534;
                margin-bottom: 2px;
                line-height: 1.1;
            }
            .bia-login-subtitle {
                color: #6b7280;
                margin-bottom: 28px;
                font-size: 15px;
            }
            .bia-login-box div[data-testid="stTextInput"] label {
                display: none;
            }
            .bia-login-box .stCheckbox {
                margin-top: 6px;
                margin-bottom: 14px;
                text-align: left;
            }
        </style>
    """, unsafe_allow_html=True)
    
    # 3) Layout centralizado
    _, col_meio, _ = st.columns([1, 2, 1])
    with col_meio:
        st.markdown('<div class="bia-login-box">', unsafe_allow_html=True)
        st.markdown('<div class="bia-login-title">💚 Bia</div>', unsafe_allow_html=True)
        st.markdown('<div class="bia-login-subtitle">Dashboard MaisLaser</div>', unsafe_allow_html=True)
        
        # Form com Enter pra submeter
        with st.form("login_form", clear_on_submit=False):
            senha = st.text_input(
                "Senha",
                type="password",
                placeholder="Digite a senha",
            )
            lembrar = st.checkbox(
                "Lembrar de mim neste dispositivo",
                value=True,
                help="Se ativo, você fica logado mesmo depois de fechar o navegador (token salvo na URL desta aba).",
            )
            submit = st.form_submit_button("Entrar", type="primary", use_container_width=True)
        
        if submit:
            if senha == st.secrets.get("DASHBOARD_PASSWORD", "maislaser"):
                st.session_state["password_correct"] = True
                if lembrar:
                    st.query_params["t"] = _expected_login_token()
                st.rerun()
            else:
                st.error("❌ Senha incorreta")
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    return False


# ============================================================================
# CONEXÃO SUPABASE
# ============================================================================

@st.cache_resource
def get_supabase() -> Client:
    """Cria cliente Supabase (cacheado)."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# ============================================================================
# CARREGAMENTO DE DADOS
# ============================================================================

@st.cache_data(ttl=20)  # cache de 20 segundos pra não martelar o Supabase
def carregar_conversas(dias_atras=7):
    """Carrega mensagens dos últimos N dias."""
    sb = get_supabase()
    data_limite = (datetime.now(TZ_SP) - timedelta(days=dias_atras)).isoformat()
    
    try:
        result = sb.table("conversas").select("*").gte("criado_em", data_limite).order("criado_em", desc=True).limit(5000).execute()
        df = pd.DataFrame(result.data)
        if not df.empty:
            df['criado_em'] = pd.to_datetime(df['criado_em'])
        return df
    except Exception as e:
        st.error(f"Erro ao carregar conversas: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=30)
def carregar_leads():
    """Carrega leads cadastrados."""
    sb = get_supabase()
    try:
        result = sb.table("leads").select("*").order("criado_em", desc=True).limit(1000).execute()
        df = pd.DataFrame(result.data)
        if not df.empty:
            df['criado_em'] = pd.to_datetime(df['criado_em'])
        return df
    except Exception as e:
        st.error(f"Erro ao carregar leads: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=10)
def carregar_configuracoes():
    """Carrega configurações do Supabase."""
    sb = get_supabase()
    try:
        result = sb.table("configuracoes").select("*").eq("id", 1).execute()
        if result.data:
            return result.data[0]
        return {}
    except Exception as e:
        st.error(f"Erro ao carregar configurações: {e}")
        return {}


def salvar_configuracoes(mogi_telefone, mogi_nome, suzano_telefone, suzano_nome, modo_manutencao):
    """Salva configurações no Supabase."""
    sb = get_supabase()
    try:
        sb.table("configuracoes").upsert({
            "id": 1,
            "mogi_telefone": mogi_telefone,
            "mogi_nome": mogi_nome,
            "suzano_telefone": suzano_telefone,
            "suzano_nome": suzano_nome,
            "modo_manutencao": modo_manutencao,
            "atualizado_em": datetime.now(TZ_SP).isoformat(),
        }).execute()
        carregar_configuracoes.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")
        return False


@st.cache_data(ttl=30)
def carregar_agendamentos():
    """Carrega agendamentos."""
    sb = get_supabase()
    try:
        result = sb.table("agendamentos").select("*").order("data_hora", desc=True).limit(500).execute()
        df = pd.DataFrame(result.data)
        if not df.empty:
            df['data_hora'] = pd.to_datetime(df['data_hora'])
            df['criado_em'] = pd.to_datetime(df['criado_em'])
        return df
    except Exception as e:
        st.error(f"Erro ao carregar agendamentos: {e}")
        return pd.DataFrame()


# ============================================================================
# AGRUPAMENTO DE CONVERSAS POR TELEFONE
# ============================================================================

def agrupar_conversas(df_conv, df_leads, df_agend=None):
    """Agrupa mensagens por telefone, retornando uma linha por conversa."""
    if df_conv.empty:
        return pd.DataFrame()
    
    # Pra cada telefone, pega a última mensagem
    df_conv_sorted = df_conv.sort_values('criado_em', ascending=False)
    
    grouped = df_conv_sorted.groupby('telefone').agg(
        ultima_mensagem=('mensagem', 'first'),
        ultimo_papel=('papel', 'first'),
        ultima_atualizacao=('criado_em', 'first'),
        primeira_atualizacao=('criado_em', 'last'),
        total_mensagens=('id', 'count'),
        total_tokens=('tokens', 'sum'),
    ).reset_index()
    
    # Limita o preview da última mensagem
    grouped['ultima_mensagem_preview'] = grouped['ultima_mensagem'].apply(
        lambda x: (x[:80] + '...') if isinstance(x, str) and len(x) > 80 else x
    )
    
    # Junta com leads pra pegar nome/unidade/status (+ transferido_em pra detecção)
    if not df_leads.empty:
        cols_lead = ['telefone', 'nome', 'unidade', 'status', 'tipo_cliente', 'genero']
        if 'transferido_em' in df_leads.columns:
            cols_lead.append('transferido_em')
        grouped = grouped.merge(
            df_leads[cols_lead],
            on='telefone',
            how='left'
        )
    else:
        grouped['nome'] = None
        grouped['unidade'] = None
        grouped['status'] = None
        grouped['tipo_cliente'] = None
        grouped['genero'] = None
    
    # Marca alertas — passa df_agend e df_leads como FONTE DA VERDADE
    grouped['alertas'] = grouped.apply(
        lambda row: detectar_alertas(row, df_conv, df_agend, df_leads),
        axis=1
    )
    
    return grouped.sort_values('ultima_atualizacao', ascending=False)


def detectar_alertas(row, df_conv, df_agend=None, df_leads_full=None):
    """Detecta possíveis problemas em uma conversa.
    
    v6 (correção crítica): usa as tabelas `agendamentos` e `leads.transferido_em`
    como FONTE DA VERDADE pra detectar desfechos — antes procurava as palavras
    'agendar' / 'transferir_...' no texto da Bia, mas o processa_resposta
    LIMPA as tags antes de salvar, então a busca falhava (ex: a Bia escrevia
    "Tá agendado", mas a regex procurava 'agendar' — palavras diferentes).
    """
    alertas = []
    
    telefone = row['telefone']
    msgs_dessa_conv = df_conv[df_conv['telefone'] == telefone].sort_values('criado_em').reset_index(drop=True)
    
    if msgs_dessa_conv.empty:
        return alertas
    
    # Concatena mensagens da Bia e do usuário (já em minúsculas)
    msgs_bia = msgs_dessa_conv[msgs_dessa_conv['papel'] == 'assistant']['mensagem'].str.lower().fillna('')
    msgs_user = msgs_dessa_conv[msgs_dessa_conv['papel'] == 'user']['mensagem'].str.lower().fillna('')
    todas_bia = ' '.join(msgs_bia.tolist())
    todas_user = ' '.join(msgs_user.tolist())
    
    # ── FONTE DA VERDADE: tabelas reais ───────────────────────────────
    # Agendamento confirmado? olha tabela `agendamentos`
    tem_agendamento = False
    if df_agend is not None and not df_agend.empty and 'telefone' in df_agend.columns:
        tem_agendamento = (df_agend['telefone'].astype(str) == str(telefone)).any()
    
    # Transferência feita? olha tabela `leads`.transferido_em
    tem_transferencia = False
    if df_leads_full is not None and not df_leads_full.empty and 'transferido_em' in df_leads_full.columns:
        match = df_leads_full[df_leads_full['telefone'].astype(str) == str(telefone)]
        if not match.empty:
            tem_transferencia = pd.notna(match.iloc[0]['transferido_em'])
    
    # ── ALERTA 1: falou de presente pra cliente existente ─────────────
    sinais_cliente_existente = ['já sou cliente', 'ja sou cliente', 'já faço aí', 'perdi a sessão', 
                                'perdi minha sessão', 'quero reagendar', 'quero remarcar', 'já fiz aí']
    cliente_se_identificou = any(s in todas_user for s in sinais_cliente_existente)
    
    if cliente_se_identificou:
        # Acha o timestamp da primeira mensagem onde o cliente se identificou
        ts_identificacao = None
        for _, msg in msgs_dessa_conv.iterrows():
            if msg['papel'] == 'user' and any(s in str(msg['mensagem']).lower() for s in sinais_cliente_existente):
                ts_identificacao = msg['criado_em']
                break
        
        # Compara por TIMESTAMP (não por índice do pandas, que é instável)
        if ts_identificacao is not None:
            msgs_bia_depois = msgs_dessa_conv[
                (msgs_dessa_conv['criado_em'] > ts_identificacao) & 
                (msgs_dessa_conv['papel'] == 'assistant')
            ]['mensagem'].str.lower().fillna('').tolist()
            
            if any('presente' in m or 'ganhou' in m or '5 sessões' in m or 'cortesia' in m for m in msgs_bia_depois):
                alertas.append('🔴 Falou de presente pra cliente existente')
    
    # ── ALERTA 2: conversa longa SEM DESFECHO REAL ────────────────────
    # Antes: procurava palavras no texto → falhava porque as tags são removidas
    # Agora: confere agendamentos/transferências como fonte da verdade
    if row['total_mensagens'] > 8 and not tem_agendamento and not tem_transferencia:
        # Fallback: respeita [ENCERRAR] caso ainda esteja no texto
        if 'encerrar' not in todas_bia:
            alertas.append('🟡 Conversa longa sem desfecho')
    
    # ── ALERTA 3: cliente perguntou preço e Bia não transferiu ────────
    if any(p in todas_user for p in ['quanto custa', 'qual o preço', 'qual o valor', 'parcelamento', 'desconto']):
        # Se já tem transferência confirmada, ok. Se não, alerta.
        if not tem_transferencia and 'coordenadora' not in todas_bia:
            alertas.append('🟠 Preço sem transferência')
    
    return alertas


# ============================================================================
# FORMATAÇÃO DA CONVERSA PRA COPIAR
# ============================================================================

def formatar_conversa_para_copiar(msgs_df, nome_cliente="Cliente"):
    """Formata a conversa no estilo WhatsApp pra copiar e colar."""
    linhas = []
    for _, msg in msgs_df.sort_values('criado_em').iterrows():
        ts = msg['criado_em']
        try:
            ts_local = ts.tz_convert(TZ_SP) if hasattr(ts, 'tz_convert') and ts.tz else ts
            ts_str = ts_local.strftime('%H:%M, %d/%m/%Y')
        except Exception:
            ts_str = str(ts)[:16]
        
        autor = nome_cliente if msg['papel'] == 'user' else "Bia"
        linhas.append(f"[{ts_str}] {autor}: {msg['mensagem']}")
    
    return '\n'.join(linhas)


# ============================================================================
# DETECÇÃO DE TAGS NA RESPOSTA DA BIA
# ============================================================================

def detectar_tags(mensagem):
    """Detecta tags do sistema em uma mensagem da Bia."""
    if not isinstance(mensagem, str):
        return []
    tags = []
    msg_lower = mensagem.lower()
    if 'transferir_coordenadora' in msg_lower or '[transferir_coordenadora]' in msg_lower:
        tags.append('🤝 Coordenadora')
    if 'transferir_humano' in msg_lower or '[transferir_humano]' in msg_lower:
        tags.append('👤 Recepção')
    if 'agendar|' in msg_lower or '[agendar' in msg_lower:
        tags.append('📅 Agendou')
    if '[encerrar]' in msg_lower:
        tags.append('✋ Encerrou')
    return tags


# ============================================================================
# RENDERIZAÇÃO DE UMA CONVERSA (DETALHE)
# ============================================================================

def renderizar_conversa(telefone, df_conv, df_leads):
    """Renderiza a tela de detalhe de uma conversa."""
    msgs = df_conv[df_conv['telefone'] == telefone].sort_values('criado_em')
    
    if msgs.empty:
        st.warning("Conversa não encontrada.")
        return
    
    # Info do lead
    lead = df_leads[df_leads['telefone'] == telefone] if not df_leads.empty else pd.DataFrame()
    
    col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
    with col1:
        nome = lead.iloc[0]['nome'] if not lead.empty and pd.notna(lead.iloc[0]['nome']) else "Sem nome"
        st.markdown(f"### 💬 {nome}")
        st.caption(f"📱 +{telefone}")
    with col2:
        if not lead.empty:
            unidade = lead.iloc[0]['unidade'] or '-'
            st.metric("Unidade", unidade.title())
    with col3:
        if not lead.empty:
            tipo = lead.iloc[0]['tipo_cliente'] or 'novo'
            st.metric("Tipo", tipo.title())
    with col4:
        st.metric("Mensagens", len(msgs))
    
    # Botões de ação
    col_btn1, col_btn2, col_btn3 = st.columns([2, 2, 6])
    with col_btn1:
        conversa_formatada = formatar_conversa_para_copiar(msgs, nome)
        st.download_button(
            "📥 Baixar conversa (.txt)",
            data=conversa_formatada,
            file_name=f"conversa_{telefone}.txt",
            mime="text/plain",
            use_container_width=True,
        )
    with col_btn2:
        st.link_button(
            "🔗 Abrir WhatsApp",
            url=f"https://wa.me/{telefone}",
            use_container_width=True,
        )
    
    # Caixa de copiar
    with st.expander("📋 **Copiar conversa formatada** (pra colar no Claude)", expanded=False):
        st.code(conversa_formatada, language="text")
        st.caption("Clique no ícone de cópia no canto superior direito do bloco acima.")
    
    st.divider()
    
    # Mensagens
    st.markdown("#### Histórico")
    for _, msg in msgs.iterrows():
        ts = msg['criado_em']
        try:
            ts_local = ts.tz_convert(TZ_SP) if hasattr(ts, 'tz_convert') and ts.tz else ts
            ts_str = ts_local.strftime('%d/%m %H:%M')
        except Exception:
            ts_str = str(ts)[:16]
        
        if msg['papel'] == 'user':
            st.markdown(
                f'<div class="msg-cliente">{msg["mensagem"]}<div class="msg-timestamp">{ts_str}</div></div>',
                unsafe_allow_html=True
            )
        else:
            tags = detectar_tags(msg['mensagem'])
            tags_html = ' '.join([f'<span class="badge-info">{t}</span>' for t in tags])
            tokens = msg.get('tokens', 0) or 0
            tokens_str = f" · {tokens} tokens" if tokens else ""
            st.markdown(
                f'<div class="msg-bia">{msg["mensagem"]}<div class="msg-timestamp">{ts_str}{tokens_str} {tags_html}</div></div>',
                unsafe_allow_html=True
            )


# ============================================================================
# TELA 1 — LISTA DE CONVERSAS
# ============================================================================

def tela_conversas(df_conv, df_leads, df_agend):
    """Lista de conversas com filtros e busca."""
    st.markdown("## 💬 Conversas")
    
    # Filtros na sidebar
    col_filt1, col_filt2, col_filt3, col_filt4 = st.columns([2, 2, 2, 3])
    with col_filt1:
        periodo = st.selectbox(
            "Período",
            ["Hoje", "Ontem", "Últimos 7 dias", "Últimos 30 dias"],
            index=2,
            key="filt_periodo"
        )
    with col_filt2:
        unidade_filt = st.selectbox(
            "Unidade",
            ["Todas", "Mogi", "Suzano"],
            key="filt_unidade"
        )
    with col_filt3:
        so_alertas = st.checkbox("⚠️ Só com alertas", key="filt_alertas")
    with col_filt4:
        busca = st.text_input("🔎 Buscar (telefone ou nome)", key="filt_busca")
    
    # Aplica filtro de período
    agora = datetime.now(TZ_SP)
    if periodo == "Hoje":
        dt_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    elif periodo == "Ontem":
        dt_inicio = (agora - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        dt_fim = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        df_conv = df_conv[df_conv['criado_em'] < dt_fim]
    elif periodo == "Últimos 7 dias":
        dt_inicio = agora - timedelta(days=7)
    else:
        dt_inicio = agora - timedelta(days=30)
    
    if not df_conv.empty and 'criado_em' in df_conv.columns:
        df_conv = df_conv[df_conv['criado_em'] >= dt_inicio]

    # Agrupa por telefone (passa df_agend pra detecção correta de desfechos)
    df_agrupado = agrupar_conversas(df_conv, df_leads, df_agend)
    
    if df_agrupado.empty:
        st.info("Nenhuma conversa no período selecionado.")
        return
    
    # Aplica filtros adicionais
    if unidade_filt != "Todas":
        df_agrupado = df_agrupado[df_agrupado['unidade'].str.contains(unidade_filt.lower(), case=False, na=False)]
    if so_alertas:
        df_agrupado = df_agrupado[df_agrupado['alertas'].apply(lambda x: len(x) > 0)]
    if busca:
        busca_lower = busca.lower()
        df_agrupado = df_agrupado[
            df_agrupado['telefone'].str.contains(busca_lower, case=False, na=False) |
            df_agrupado['nome'].fillna('').str.contains(busca_lower, case=False, na=False)
        ]
    
    # Métricas rápidas
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Conversas", len(df_agrupado))
    col_m2.metric("Mensagens", int(df_agrupado['total_mensagens'].sum()) if not df_agrupado.empty else 0)
    col_m3.metric("Com alertas", int(df_agrupado['alertas'].apply(lambda x: len(x) > 0).sum()))
    tokens_total = int(df_agrupado['total_tokens'].sum()) if not df_agrupado.empty else 0
    custo = (tokens_total / 1_000_000) * CUSTO_USD_POR_MTOK
    col_m4.metric("Custo IA", f"US$ {custo:.3f}")
    
    st.divider()
    
    # Tabela
    if df_agrupado.empty:
        st.info("Nada por aqui com esses filtros.")
        return
    
    st.markdown("### Lista")
    st.caption(f"📊 {len(df_agrupado)} conversa(s) · clique em **Ver detalhes** pra abrir")
    
    # Header
    h1, h2, h3, h4, h5, h6 = st.columns([2, 1.5, 1, 3, 1, 1.2])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Última msg**")
    h5.markdown("**Quando**")
    h6.markdown("**Ação**")
    st.divider()
    
    for idx, row in df_agrupado.head(50).iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([2, 1.5, 1, 3, 1, 1.2])
        
        nome_display = row['nome'] if pd.notna(row.get('nome')) else "Sem nome"
        c1.markdown(f"**{nome_display}**")
        if row['alertas']:
            for a in row['alertas']:
                c1.markdown(f'<span class="badge-alerta">{a}</span>', unsafe_allow_html=True)
        
        c2.text(f"+{row['telefone']}")
        c3.text(str(row['unidade']).title() if pd.notna(row.get('unidade')) and row.get('unidade') else '-')
        
        papel_emoji = "👤" if row['ultimo_papel'] == 'user' else "💚"
        c4.text(f"{papel_emoji} {row['ultima_mensagem_preview']}")
        
        try:
            ts_local = row['ultima_atualizacao'].tz_convert(TZ_SP)
            agora_aware = datetime.now(TZ_SP)
            delta = agora_aware - ts_local
            if delta.total_seconds() < 60:
                tempo_str = "agora"
            elif delta.total_seconds() < 3600:
                tempo_str = f"{int(delta.total_seconds() / 60)}min"
            elif delta.total_seconds() < 86400:
                tempo_str = f"{int(delta.total_seconds() / 3600)}h"
            else:
                tempo_str = ts_local.strftime('%d/%m')
        except Exception:
            tempo_str = "-"
        c5.text(tempo_str)
        
        if c6.button("Ver detalhes", key=f"btn_{row['telefone']}_{idx}"):
            st.session_state['conversa_selecionada'] = row['telefone']
            st.rerun()
    
    if len(df_agrupado) > 50:
        st.caption(f"Mostrando 50 de {len(df_agrupado)} conversas. Use os filtros pra refinar.")


# ============================================================================
# TELA 2 — MÉTRICAS
# ============================================================================

def tela_metricas(df_conv, df_leads, df_agend):
    """Tela de métricas e gráficos."""
    st.markdown("## 📈 Métricas")
    
    # Filtro de período
    periodo = st.selectbox(
        "Período de análise",
        ["Hoje", "Últimos 7 dias", "Últimos 30 dias"],
        index=1,
        key="met_periodo"
    )
    
    agora = datetime.now(TZ_SP)
    if periodo == "Hoje":
        dt_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        dias = 1
    elif periodo == "Últimos 7 dias":
        dt_inicio = agora - timedelta(days=7)
        dias = 7
    else:
        dt_inicio = agora - timedelta(days=30)
        dias = 30
    
    if not df_conv.empty and 'criado_em' in df_conv.columns:
        df_conv_p = df_conv[df_conv['criado_em'] >= dt_inicio]
    else:
        df_conv_p = df_conv
    
    # ─── Cards de topo ──────────────────────────────────────────────
    st.markdown("### Resumo do período")
    
    conversas_unicas = df_conv_p['telefone'].nunique() if not df_conv_p.empty and 'telefone' in df_conv_p.columns else 0
    total_msgs = len(df_conv_p)
    msgs_bia = len(df_conv_p[df_conv_p['papel'] == 'assistant']) if not df_conv_p.empty and 'papel' in df_conv_p.columns else 0
    tokens_total = int(df_conv_p['tokens'].sum()) if not df_conv_p.empty and 'tokens' in df_conv_p.columns else 0
    custo_usd = (tokens_total / 1_000_000) * CUSTO_USD_POR_MTOK
    custo_brl = custo_usd * 5.50
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Conversas únicas", conversas_unicas)
    c2.metric("Mensagens totais", total_msgs)
    c3.metric("Respostas Bia", msgs_bia)
    c4.metric("Custo IA", f"R$ {custo_brl:.2f}", help=f"US$ {custo_usd:.4f} · {tokens_total:,} tokens")
    
    st.divider()
    
    # ─── Funil de conversão ─────────────────────────────────────────
    st.markdown("### 🎯 Funil de conversão")
    
    if df_conv_p.empty:
        st.info("Sem dados no período.")
    else:
        # Iniciaram conversa = telefones únicos
        iniciaram = conversas_unicas
        
        # Engajaram = conversas com 3+ mensagens
        if not df_conv_p.empty and 'telefone' in df_conv_p.columns:
            msgs_por_tel = df_conv_p.groupby('telefone').size()
            engajaram = (msgs_por_tel >= 3).sum()
        else:
            engajaram = 0
        
        # Transferiram = conversas com tag de transferência
        transferiram = 0
        agendaram = 0
        if not df_conv_p.empty and 'papel' in df_conv_p.columns:
            df_conv_bia = df_conv_p[df_conv_p['papel'] == 'assistant']
            for tel in df_conv_p['telefone'].unique():
                msgs_tel = df_conv_bia[df_conv_bia['telefone'] == tel]['mensagem'].str.lower().fillna('')
                todas = ' '.join(msgs_tel.tolist())
                if 'transferir_coordenadora' in todas or 'transferir_humano' in todas:
                    transferiram += 1
                if 'agendar|' in todas or '[agendar' in todas:
                    agendaram += 1
        
        fig = go.Figure(go.Funnel(
            y=["Iniciaram conversa", "Engajaram (3+ msgs)", "Transferiram", "Agendaram"],
            x=[iniciaram, engajaram, transferiram, agendaram],
            textinfo="value+percent initial",
            marker={"color": [COR_PRIMARIA, "#16a34a", "#15803d", "#14532d"]}
        ))
        fig.update_layout(height=350, margin=dict(t=20, b=20, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)
    
    st.divider()
    
    # ─── Gráficos lado a lado ────────────────────────────────────────
    col_g1, col_g2 = st.columns(2)
    
    with col_g1:
        st.markdown("### 📅 Conversas por hora do dia")
        if not df_conv_p.empty and 'papel' in df_conv_p.columns:
            df_user = df_conv_p[df_conv_p['papel'] == 'user'].copy()
            if not df_user.empty:
                df_user['hora'] = df_user['criado_em'].dt.tz_convert(TZ_SP).dt.hour
                hora_count = df_user.groupby('hora').size().reindex(range(24), fill_value=0).reset_index()
                hora_count.columns = ['Hora', 'Mensagens']
                fig = px.bar(hora_count, x='Hora', y='Mensagens', color_discrete_sequence=[COR_PRIMARIA])
                fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Sem dados.")
        else:
            st.info("Sem dados.")
    
    with col_g2:
        st.markdown("### 🏢 Comparativo unidades")
        if not df_leads.empty and 'criado_em' in df_leads.columns:
            df_leads_p = df_leads[df_leads['criado_em'] >= dt_inicio]
            if not df_leads_p.empty:
                unidade_count = df_leads_p['unidade'].fillna('desconhecido').value_counts().reset_index()
                unidade_count.columns = ['Unidade', 'Leads']
                fig = px.pie(unidade_count, values='Leads', names='Unidade',
                             color_discrete_sequence=[COR_PRIMARIA, "#3b82f6", "#a3a3a3"])
                fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Sem leads no período.")
        else:
            st.info("Sem leads cadastrados.")
    
    st.divider()
    
    # ─── Custo IA por dia ───────────────────────────────────────────
    st.markdown("### 💰 Custo IA por dia")
    if not df_conv_p.empty:
        df_copia = df_conv_p.copy()
        df_copia['dia'] = df_copia['criado_em'].dt.tz_convert(TZ_SP).dt.date
        custo_dia = df_copia.groupby('dia')['tokens'].sum().reset_index()
        custo_dia['Custo USD'] = (custo_dia['tokens'] / 1_000_000) * CUSTO_USD_POR_MTOK
        custo_dia['Custo BRL'] = custo_dia['Custo USD'] * 5.50
        custo_dia.columns = ['Dia', 'Tokens', 'Custo USD', 'Custo BRL']
        fig = px.line(custo_dia, x='Dia', y='Custo BRL', markers=True,
                      color_discrete_sequence=[COR_PRIMARIA])
        fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10),
                          yaxis_title="Custo (R$)")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados.")
    
    st.divider()
    
    # ─── Top motivos de transferência ──────────────────────────────
    st.markdown("### 🔄 Motivos de transferência / encerramento")
    if not df_conv_p.empty and 'papel' in df_conv_p.columns:
        df_bia = df_conv_p[df_conv_p['papel'] == 'assistant']
        motivos = {
            "🤝 Coordenadora": 0,
            "👤 Recepção (humano)": 0,
            "📅 Agendou": 0,
            "✋ Encerrou": 0,
            "Sem desfecho": 0,
        }
        for tel in df_conv_p['telefone'].unique():
            msgs_tel = df_bia[df_bia['telefone'] == tel]['mensagem'].str.lower().fillna('')
            todas = ' '.join(msgs_tel.tolist())
            if 'transferir_coordenadora' in todas:
                motivos["🤝 Coordenadora"] += 1
            elif 'transferir_humano' in todas:
                motivos["👤 Recepção (humano)"] += 1
            elif 'agendar|' in todas or '[agendar' in todas:
                motivos["📅 Agendou"] += 1
            elif '[encerrar]' in todas:
                motivos["✋ Encerrou"] += 1
            else:
                motivos["Sem desfecho"] += 1
        
        df_motivos = pd.DataFrame(list(motivos.items()), columns=['Motivo', 'Conversas'])
        fig = px.bar(df_motivos, x='Conversas', y='Motivo', orientation='h',
                     color_discrete_sequence=[COR_PRIMARIA])
        fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados.")
    
    st.divider()
    
    # ─── Conversas problemáticas ───────────────────────────────────
    st.markdown("### ⚠️ Conversas que precisam de atenção")
    df_agrupado_p = agrupar_conversas(df_conv_p, df_leads, df_agend)
    if not df_agrupado_p.empty:
        problematicas = df_agrupado_p[df_agrupado_p['alertas'].apply(lambda x: len(x) > 0)]
        if not problematicas.empty:
            st.caption(f"{len(problematicas)} conversa(s) com sinais de problema — ideal pra revisar e melhorar o cérebro")
            for _, row in problematicas.head(10).iterrows():
                with st.expander(f"📱 +{row['telefone']} · {row.get('nome', 'Sem nome')} · {row['total_mensagens']} msgs"):
                    for a in row['alertas']:
                        st.markdown(f"- {a}")
                    st.caption(f"Última: {row['ultima_mensagem_preview']}")
                    if st.button("Ver conversa completa", key=f"prob_{row['telefone']}"):
                        st.session_state['conversa_selecionada'] = row['telefone']
                        st.rerun()
        else:
            st.success("🎉 Nenhuma conversa problemática detectada no período!")


# ============================================================================
# TELA 3 — CONFIGURAÇÕES
# ============================================================================

def tela_configuracoes():
    """Tela de configurações da Bia — integrada com Supabase."""
    st.markdown("## ⚙️ Configurações")

    # Carrega configs atuais do Supabase
    cfg = carregar_configuracoes()

    st.success("✅ Configurações integradas ao Supabase — ao salvar, o n8n vai buscar os números automaticamente.")

    st.markdown("### 📞 WhatsApp das coordenadoras de vendas")
    st.caption("Quando a Bia disparar [TRANSFERIR_COORDENADORA], ela vai mandar um aviso pra esses números.")

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown("**Mogi das Cruzes**")
        coord_mogi = st.text_input(
            "Número (com DDD, só dígitos)",
            value=cfg.get('mogi_telefone', ''),
            key='input_coord_mogi',
            placeholder="11999999999"
        )
        nome_coord_mogi = st.text_input(
            "Nome da coordenadora",
            value=cfg.get('mogi_nome', ''),
            key='input_nome_coord_mogi',
            placeholder="Ex: Beatriz"
        )
    with col_c2:
        st.markdown("**Suzano**")
        coord_suzano = st.text_input(
            "Número (com DDD, só dígitos)",
            value=cfg.get('suzano_telefone', ''),
            key='input_coord_suzano',
            placeholder="11999999999"
        )
        nome_coord_suzano = st.text_input(
            "Nome da coordenadora",
            value=cfg.get('suzano_nome', ''),
            key='input_nome_coord_suzano',
            placeholder="Ex: Rafaela"
        )

    st.divider()

    st.markdown("### 🛠️ Modo manutenção")
    manutencao = st.toggle(
        "Pausar a Bia (ela para de responder)",
        value=cfg.get('modo_manutencao', False),
        help="Quando ligado, o n8n vai checar essa flag e não processar novas mensagens."
    )
    if manutencao:
        st.warning("⚠️ Modo manutenção ATIVO — a Bia vai parar de responder quando o n8n checar essa flag.")

    st.divider()

    if st.button("💾 Salvar no Supabase", type="primary"):
        ok = salvar_configuracoes(
            mogi_telefone=coord_mogi,
            mogi_nome=nome_coord_mogi,
            suzano_telefone=coord_suzano,
            suzano_nome=nome_coord_suzano,
            modo_manutencao=manutencao,
        )
        if ok:
            st.success("✅ Salvo! O n8n vai usar esses dados na próxima transferência.")
            st.balloons()

    st.divider()

    st.markdown("### 📊 Informações do sistema")
    col_i1, col_i2 = st.columns(2)
    with col_i1:
        st.metric("Versão do cérebro", "v3.7")
        st.metric("Modelo Claude", "claude-haiku-4-5")
    with col_i2:
        st.metric("Webhook n8n", "✅ Online")
        st.caption("https://maislaser-robo.app.n8n.cloud/webhook/maislaser-whatsapp")

    if cfg.get('atualizado_em'):
        st.caption(f"Última atualização das configs: {cfg['atualizado_em'][:19].replace('T', ' ')}")


# ============================================================================
# MAIN
# ============================================================================
# TELA: TRANSFERÊNCIAS PRA COORDENADORA
# ============================================================================

def tela_transferencias(df_leads, df_conv):
    """Lista todas as transferências feitas pra coordenadoras."""
    st.markdown("# 🔥 Transferências")
    st.caption("Leads que a Bia encaminhou pras coordenadoras de venda")
    
    # Filtra apenas leads com transferência
    if df_leads is None or df_leads.empty or 'transferido_em' not in df_leads.columns:
        st.info("📭 Nenhuma transferência registrada ainda. Quando a Bia transferir o primeiro lead, ele aparecerá aqui.")
        return
    
    df_transf = df_leads[df_leads['transferido_em'].notna()].copy()
    
    if df_transf.empty:
        st.info("📭 Nenhuma transferência registrada ainda. Quando a Bia transferir o primeiro lead, ele aparecerá aqui.")
        return
    
    # Converte timestamps pra fuso SP
    df_transf['transferido_em'] = pd.to_datetime(df_transf['transferido_em'])
    try:
        df_transf['transferido_em_sp'] = df_transf['transferido_em'].dt.tz_convert(TZ_SP)
    except Exception:
        df_transf['transferido_em_sp'] = df_transf['transferido_em']
    
    df_transf = df_transf.sort_values('transferido_em', ascending=False)
    
    # ── BOTÕES SEGMENTADOS DE UNIDADE (estilo iOS) ──
    st.markdown("")  # espaço
    
    # CSS pra deixar os botões iguais (segmented control)
    st.markdown("""
        <style>
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] button[kind="primary"] {
            background-color: #22c55e !important;
            color: white !important;
            border-color: #22c55e !important;
            font-weight: 600 !important;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] button[kind="secondary"] {
            background-color: #f3f4f6 !important;
            color: #374151 !important;
            border-color: #e5e7eb !important;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Estado da seleção da unidade
    if 'transf_unidade_btn' not in st.session_state:
        st.session_state['transf_unidade_btn'] = "Todas"
    
    # Calcula contagens por unidade pra mostrar no botão
    cnt_todas = len(df_transf)
    cnt_mogi = len(df_transf[df_transf['unidade'] == 'Mogi das Cruzes'])
    cnt_suzano = len(df_transf[df_transf['unidade'] == 'Suzano'])
    
    btn_col1, btn_col2, btn_col3, _ = st.columns([1.2, 1.6, 1.2, 4])
    
    with btn_col1:
        is_todas = st.session_state['transf_unidade_btn'] == "Todas"
        if st.button(f"🏢 Todas ({cnt_todas})", 
                     type="primary" if is_todas else "secondary",
                     use_container_width=True,
                     key="btn_unid_todas"):
            st.session_state['transf_unidade_btn'] = "Todas"
            st.rerun()
    
    with btn_col2:
        is_mogi = st.session_state['transf_unidade_btn'] == "Mogi das Cruzes"
        if st.button(f"📍 Mogi das Cruzes ({cnt_mogi})", 
                     type="primary" if is_mogi else "secondary",
                     use_container_width=True,
                     key="btn_unid_mogi"):
            st.session_state['transf_unidade_btn'] = "Mogi das Cruzes"
            st.rerun()
    
    with btn_col3:
        is_suzano = st.session_state['transf_unidade_btn'] == "Suzano"
        if st.button(f"📍 Suzano ({cnt_suzano})", 
                     type="primary" if is_suzano else "secondary",
                     use_container_width=True,
                     key="btn_unid_suzano"):
            st.session_state['transf_unidade_btn'] = "Suzano"
            st.rerun()
    
    unidade_filtro = st.session_state['transf_unidade_btn']
    
    st.markdown("")  # espaço
    
    # ── FILTROS MENORES (período e coordenadora) ──
    col1, col2 = st.columns(2)
    
    with col1:
        periodo = st.selectbox(
            "Período",
            ["Últimas 24h", "Últimos 7 dias", "Últimos 30 dias", "Tudo"],
            index=1,
            key="transf_periodo"
        )
    
    with col2:
        coordenadoras = ["Todas"] + sorted(df_transf['transferido_para'].dropna().unique().tolist())
        coordenadora_filtro = st.selectbox("Coordenadora", coordenadoras, key="transf_coord")
    
    # Aplica filtros
    agora = datetime.now(TZ_SP)
    if periodo == "Últimas 24h":
        cutoff = agora - timedelta(hours=24)
    elif periodo == "Últimos 7 dias":
        cutoff = agora - timedelta(days=7)
    elif periodo == "Últimos 30 dias":
        cutoff = agora - timedelta(days=30)
    else:
        cutoff = None
    
    df_filtrado = df_transf.copy()
    if cutoff is not None:
        df_filtrado = df_filtrado[df_filtrado['transferido_em_sp'] >= cutoff]
    
    if coordenadora_filtro != "Todas":
        df_filtrado = df_filtrado[df_filtrado['transferido_para'] == coordenadora_filtro]
    
    if unidade_filtro != "Todas":
        df_filtrado = df_filtrado[df_filtrado['unidade'] == unidade_filtro]
    
    # ── CARDS DE RESUMO ──
    st.divider()
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total no período", len(df_filtrado))
    
    with col2:
        if 'transferido_para' in df_filtrado.columns:
            top_coord = df_filtrado['transferido_para'].value_counts()
            top_nome = top_coord.index[0] if len(top_coord) > 0 else "—"
            top_qtd = top_coord.iloc[0] if len(top_coord) > 0 else 0
            st.metric(f"Top: {top_nome}", f"{top_qtd} leads")
    
    with col3:
        avisados = df_filtrado['cliente_avisado'].sum() if 'cliente_avisado' in df_filtrado.columns else 0
        st.metric("Cliente avisado ✅", int(avisados))
    
    with col4:
        nao_avisados = len(df_filtrado) - (df_filtrado['cliente_avisado'].sum() if 'cliente_avisado' in df_filtrado.columns else 0)
        st.metric("Pendente aviso ⚠️", int(nao_avisados))
    
    # ── LISTA ──
    st.divider()
    st.markdown(f"### 📋 Lista — {len(df_filtrado)} transferência(s)")
    
    if df_filtrado.empty:
        st.info("Nenhuma transferência no período/filtros selecionados.")
        return
    
    # Cabeçalho
    h_col1, h_col2, h_col3, h_col4, h_col5, h_col6 = st.columns([1.5, 1.5, 1.2, 1.2, 3, 1])
    h_col1.markdown("**Cliente**")
    h_col2.markdown("**Telefone**")
    h_col3.markdown("**Unidade**")
    h_col4.markdown("**Coordenadora**")
    h_col5.markdown("**Sinal de compra**")
    h_col6.markdown("**Quando**")
    st.divider()
    
    # Linhas
    for _, lead in df_filtrado.iterrows():
        col1, col2, col3, col4, col5, col6 = st.columns([1.5, 1.5, 1.2, 1.2, 3, 1])
        
        nome = lead.get('nome') or "Sem nome"
        telefone = lead.get('telefone', '—')
        unidade = lead.get('unidade') or '—'
        coord = lead.get('transferido_para') or '—'
        sinal = lead.get('ultimo_sinal_compra') or '—'
        if isinstance(sinal, str) and len(sinal) > 60:
            sinal = sinal[:60] + "..."
        
        try:
            quando = lead['transferido_em_sp'].strftime('%d/%m %H:%M')
        except Exception:
            quando = '—'
        
        avisado_emoji = " ✅" if lead.get('cliente_avisado') else " ⚠️"
        
        col1.write(f"{nome}{avisado_emoji}")
        col2.write(f"+{telefone}" if not telefone.startswith('+') else telefone)
        col3.write(unidade)
        col4.write(coord)
        col5.write(f"💬 _{sinal}_" if sinal != '—' else '—')
        col6.write(quando)
        
        # Botão pra ver conversa
        if st.button("Ver conversa", key=f"ver_transf_{telefone}_{lead.name}"):
            st.session_state['conversa_selecionada'] = telefone
            st.rerun()
        
        st.markdown("---")


# ============================================================================
# TELA: AGENDAMENTOS (sessões de cortesia agendadas via Google Calendar)
# ============================================================================

def tela_agendamentos(df_agend, df_leads, df_conv):
    """Lista todos os agendamentos criados pela Bia."""
    st.markdown("# 📅 Agendamentos")
    st.caption("Sessões de cortesia agendadas pela Bia via Google Calendar")
    
    if df_agend is None or df_agend.empty:
        st.info("📭 Nenhum agendamento registrado ainda. Quando a Bia agendar a primeira cortesia, ela aparecerá aqui.")
        return
    
    df = df_agend.copy()
    
    # Normaliza timestamps pro fuso SP
    try:
        df['data_hora_sp'] = df['data_hora'].dt.tz_convert(TZ_SP)
    except Exception:
        df['data_hora_sp'] = df['data_hora']
    
    # Normaliza unidade (banco pode ter 'Mogi', 'Mogi das Cruzes', 'mogi', etc)
    def _norm_unidade(u):
        if not isinstance(u, str):
            return 'desconhecida'
        ul = u.lower().strip()
        if 'mogi' in ul or 'monte' in ul:
            return 'Mogi'
        elif 'suzano' in ul:
            return 'Suzano'
        return u
    
    df['unidade_norm'] = df['unidade'].apply(_norm_unidade)
    
    # ── BOTÕES SEGMENTADOS DE UNIDADE (mesmo estilo da aba Transferências) ──
    # CSS dos botões (reaproveita o estilo já injetado na tela de transferências
    # mas garante caso o usuário entre direto aqui)
    st.markdown("""
        <style>
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] button[kind="primary"] {
            background-color: #22c55e !important;
            color: white !important;
            border-color: #22c55e !important;
            font-weight: 600 !important;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] button[kind="secondary"] {
            background-color: #f3f4f6 !important;
            color: #374151 !important;
            border-color: #e5e7eb !important;
        }
        </style>
    """, unsafe_allow_html=True)
    
    if 'agend_unidade_btn' not in st.session_state:
        st.session_state['agend_unidade_btn'] = "Todas"
    
    cnt_todas = len(df)
    cnt_mogi = int((df['unidade_norm'] == 'Mogi').sum())
    cnt_suzano = int((df['unidade_norm'] == 'Suzano').sum())
    
    btn_col1, btn_col2, btn_col3, _ = st.columns([1.2, 1.4, 1.2, 4])
    
    with btn_col1:
        is_todas = st.session_state['agend_unidade_btn'] == "Todas"
        if st.button(f"🏢 Todas ({cnt_todas})",
                     type="primary" if is_todas else "secondary",
                     use_container_width=True,
                     key="btn_agend_todas"):
            st.session_state['agend_unidade_btn'] = "Todas"
            st.rerun()
    
    with btn_col2:
        is_mogi = st.session_state['agend_unidade_btn'] == "Mogi"
        if st.button(f"📍 Mogi ({cnt_mogi})",
                     type="primary" if is_mogi else "secondary",
                     use_container_width=True,
                     key="btn_agend_mogi"):
            st.session_state['agend_unidade_btn'] = "Mogi"
            st.rerun()
    
    with btn_col3:
        is_suzano = st.session_state['agend_unidade_btn'] == "Suzano"
        if st.button(f"📍 Suzano ({cnt_suzano})",
                     type="primary" if is_suzano else "secondary",
                     use_container_width=True,
                     key="btn_agend_suzano"):
            st.session_state['agend_unidade_btn'] = "Suzano"
            st.rerun()
    
    unidade_filtro = st.session_state['agend_unidade_btn']
    
    st.markdown("")  # espaço
    
    # ── FILTROS DE PERÍODO E STATUS ──
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        periodo = st.selectbox(
            "Período",
            ["Próximos (hoje em diante)", "Hoje", "Próximos 7 dias", "Últimos 30 dias", "Tudo"],
            index=0,
            key="agend_periodo"
        )
    with col_f2:
        if 'status' in df.columns:
            status_opcoes = ["Todos"] + sorted(df['status'].dropna().unique().tolist())
        else:
            status_opcoes = ["Todos"]
        status_filtro = st.selectbox("Status", status_opcoes, key="agend_status")
    
    # Aplica filtros
    agora = datetime.now(TZ_SP)
    hoje_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    df_filt = df.copy()
    
    if periodo == "Próximos (hoje em diante)":
        df_filt = df_filt[df_filt['data_hora_sp'] >= hoje_inicio]
    elif periodo == "Hoje":
        hoje_fim = hoje_inicio + timedelta(days=1)
        df_filt = df_filt[(df_filt['data_hora_sp'] >= hoje_inicio) & (df_filt['data_hora_sp'] < hoje_fim)]
    elif periodo == "Próximos 7 dias":
        df_filt = df_filt[(df_filt['data_hora_sp'] >= hoje_inicio) & (df_filt['data_hora_sp'] <= agora + timedelta(days=7))]
    elif periodo == "Últimos 30 dias":
        df_filt = df_filt[(df_filt['data_hora_sp'] >= agora - timedelta(days=30)) & (df_filt['data_hora_sp'] <= agora)]
    # "Tudo" → sem filtro de período
    
    if status_filtro != "Todos" and 'status' in df_filt.columns:
        df_filt = df_filt[df_filt['status'] == status_filtro]
    
    if unidade_filtro != "Todas":
        df_filt = df_filt[df_filt['unidade_norm'] == unidade_filtro]
    
    # Ordena: futuros mais próximos primeiro; passados, mais recentes primeiro
    if periodo in ["Próximos (hoje em diante)", "Hoje", "Próximos 7 dias"]:
        df_filt = df_filt.sort_values('data_hora', ascending=True)
    else:
        df_filt = df_filt.sort_values('data_hora', ascending=False)
    
    # ── CARDS DE RESUMO ──
    st.divider()
    col1, col2, col3, col4 = st.columns(4)
    
    col1.metric("Total no filtro", len(df_filt))
    
    if 'status' in df_filt.columns and not df_filt.empty:
        status_lower = df_filt['status'].astype(str).str.lower()
        confirmados = int((status_lower == 'confirmado').sum())
        pendentes = int((status_lower == 'agendado').sum())
        col2.metric("Confirmados ✅", confirmados)
        col3.metric("Pendente confirmar ⏳", pendentes)
    else:
        col2.metric("Confirmados", "—")
        col3.metric("Pendentes", "—")
    
    try:
        proximos_7 = int(((df_filt['data_hora_sp'] >= hoje_inicio) & 
                          (df_filt['data_hora_sp'] <= agora + timedelta(days=7))).sum())
    except Exception:
        proximos_7 = 0
    col4.metric("Próximos 7 dias", proximos_7)
    
    # ── LISTA ──
    st.divider()
    st.markdown(f"### 📋 Lista — {len(df_filt)} agendamento(s)")
    
    if df_filt.empty:
        st.info("Nenhum agendamento com esses filtros.")
        return
    
    # Cabeçalho
    h1, h2, h3, h4, h5, h6, h7 = st.columns([1.6, 1.4, 0.9, 1.2, 1.4, 1.1, 0.9])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Área**")
    h5.markdown("**Quando**")
    h6.markdown("**Status**")
    h7.markdown("**Ação**")
    st.divider()
    
    status_emoji_map = {
        'agendado': '📅 Agendado',
        'confirmado': '✅ Confirmado',
        'cancelado': '❌ Cancelado',
        'realizado': '🎉 Realizado',
        'faltou': '😶 Faltou',
        'no_show': '😶 Faltou',
    }
    
    for _, ag in df_filt.iterrows():
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.6, 1.4, 0.9, 1.2, 1.4, 1.1, 0.9])
        
        nome = ag.get('nome') or '—'
        telefone = str(ag.get('telefone', '—'))
        unidade = ag.get('unidade_norm') or '—'
        area = ag.get('area') or '—'
        fazer = bool(ag.get('fazer_na_hora', False))
        status = str(ag.get('status') or 'agendado').lower()
        
        try:
            quando = ag['data_hora_sp'].strftime('%d/%m %H:%M')
        except Exception:
            quando = '—'
        
        # Destaca agendamentos futuros com ⏭️
        is_futuro = False
        try:
            is_futuro = ag['data_hora_sp'] >= agora
        except Exception:
            pass
        prefixo = "⏭️ " if is_futuro else ""
        
        c1.write(f"{prefixo}{nome}")
        c2.write(f"+{telefone}" if not telefone.startswith('+') else telefone)
        c3.write(unidade)
        area_str = f"{area}" + (" ⚡" if fazer else "")  # ⚡ = quer fazer na hora
        c4.write(area_str)
        c5.write(quando)
        c6.write(status_emoji_map.get(status, status))
        
        if c7.button("Ver", key=f"ver_agend_{telefone}_{ag.name}"):
            st.session_state['conversa_selecionada'] = telefone
            st.rerun()
        
        st.markdown("---")
    
    st.caption("⚡ = cliente quer fazer a sessão na hora (vir com a área depilada na lâmina)  ·  ⏭️ = agendamento futuro")


# ============================================================================

def main():
    if not check_password():
        st.stop()
    
    # Sidebar
    with st.sidebar:
        st.markdown("# 💚 Bia")
        st.caption("Dashboard MaisLaser")
        st.divider()
        
        if st.button("🔄 Atualizar dados", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        
        auto_refresh = st.checkbox("Auto-refresh a cada 30s", value=False)
        
        st.divider()
        st.caption("**Versão Cérebro:** v3.7")
        st.caption("**Modelo:** claude-haiku-4-5")
        
        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            st.session_state["password_correct"] = False
            # Limpa token persistente da URL (forçando login na próxima vez)
            if "t" in st.query_params:
                del st.query_params["t"]
            st.rerun()
    
    # Carrega dados
    with st.spinner("Carregando dados..."):
        df_conv = carregar_conversas(dias_atras=30)
        df_leads = carregar_leads()
        df_agend = carregar_agendamentos()
    
    # Se tem conversa selecionada, mostra detalhe
    if 'conversa_selecionada' in st.session_state and st.session_state['conversa_selecionada']:
        if st.button("← Voltar pra lista"):
            del st.session_state['conversa_selecionada']
            st.rerun()
        renderizar_conversa(st.session_state['conversa_selecionada'], df_conv, df_leads)
    else:
        # Abas principais — agora com Agendamentos entre Transferências e Métricas
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "💬 Conversas",
            "🔥 Transferências",
            "📅 Agendamentos",
            "📈 Métricas",
            "⚙️ Configurações",
        ])
        
        with tab1:
            tela_conversas(df_conv, df_leads, df_agend)
        
        with tab2:
            tela_transferencias(df_leads, df_conv)
        
        with tab3:
            tela_agendamentos(df_agend, df_leads, df_conv)
        
        with tab4:
            tela_metricas(df_conv, df_leads, df_agend)
        
        with tab5:
            tela_configuracoes()
    
    # Auto refresh
    if auto_refresh:
        time.sleep(30)
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
