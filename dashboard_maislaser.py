"""
==============================================================================
DASHBOARD MAISLASER — Bia
==============================================================================
v5.2 (22/06/2026): botão "🔗 Nova aba" na lista de conversas — abre a
conversa em outra guia do navegador (preserva o token de login).
v5.1 (22/06/2026): fallback bia_disparos pra nome/unidade na aba Conversas.
Indicados do Indique e Ganhe (Bia v5) agora aparecem com nome+unidade certos,
filtros Mogi/Suzano pegam todos eles.
==============================================================================
"""

# ============================================================================
# 1) IMPORTS
# ============================================================================

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import time
import hashlib
import base64
import os

# Módulos das abas novas (Fase 7+)
from aba_base_clientes import render_aba_base_clientes
from aba_confirmacao import (
    tela_confirmacao_disparos_dia,
    tela_confirmacao_historico,
    tela_confirmacao_indicacoes,
    tela_confirmacao_metricas,
)
from aba_disparador import render_aba_disparador
from aba_zapi import render_aba_zapi_aguardando, render_aba_zapi_ranking, render_aba_zapi_indicacoes, render_aba_zapi_metricas, render_aba_zapi_clientes
from aba_pendencias import render_aba_pendencias
from aba_historico_disparos import render_aba_historico_disparos
from aba_historico_bia import render_aba_historico_bia


# ============================================================================
# 2) CONFIG INICIAL
# ============================================================================

st.set_page_config(
    page_title="Bia · Dashboard MaisLaser",
    page_icon="💚",
    layout="wide",
    initial_sidebar_state="expanded",
)

TZ_SP = timezone(timedelta(hours=-3))
COR_PRIMARIA = "#5BC0BE"      # teal do logo Maislaser
COR_PRIMARIA_DARK = "#3D9991"
CUSTO_USD_POR_MTOK = 3.0
VERSAO_DASHBOARD = "v5.2"
VERSAO_CEREBRO = "v3.10"
VERSAO_APPS_SCRIPT = "v6.5"
MODELO_CLAUDE_DEFAULT = "claude-sonnet-4-6"

# Robôs disponíveis no dashboard (centralizando tudo num só lugar)
ROBOS = {
    'bia':         '🤖 Robô Bia IA',
    'confirmacao': '📅 Robô Confirmação Agenda',
    'zapi':        '🎁 Robô Z-API Indicações',
}


# ============================================================================
# 3) CSS — visual moderno (paleta teal do logo)
# ============================================================================

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
    --primary: #5BC0BE;
    --primary-dark: #3D9991;
    --primary-light: #A0D9D7;
    --primary-bg: #ECFAF9;
    --green: #22c55e;
    --red: #ef4444;
    --amber: #f59e0b;
    --blue: #3b82f6;
    --text: #1A2332;
    --text-secondary: #6B7280;
    --text-muted: #9CA3AF;
    --bg-page: #F7F9FC;
    --bg-card: #FFFFFF;
    --border: #E5E7EB;
    --border-light: #F3F4F6;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
    --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
    --shadow-lg: 0 10px 30px rgba(0,0,0,0.10);
    --radius: 12px;
    --radius-sm: 8px;
    --radius-lg: 16px;
}

/* App body — IMPORTANTE: NÃO aplicar Inter em '*' porque atropela Material Symbols dos ícones */
.stApp { background-color: var(--bg-page); font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
/* Aplica Inter só em elementos de texto, NUNCA em ícones */
.stApp p, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp label, .stApp button, .stApp input, .stApp textarea, .stApp select { font-family: 'Inter', sans-serif; }
/* Preserva fonte dos ícones do Streamlit (Material Symbols) */
[data-testid="stIconMaterial"], .material-icons, .material-symbols-outlined,
[class*="material-symbols"], [class*="MaterialSymbols"] { font-family: 'Material Symbols Outlined', 'Material Symbols Rounded', 'Material Icons' !important; }

/* Headers */
h1, h2, h3, h4 { color: var(--text); font-weight: 700 !important; letter-spacing: -0.02em; }
h1 { font-size: 28px !important; }
h2 { font-size: 22px !important; }
h3 { font-size: 18px !important; }

/* ===== SIDEBAR ===== */
[data-testid="stSidebar"] { background: linear-gradient(180deg, #5BC0BE 0%, #3D9991 100%); border-right: none; }
[data-testid="stSidebar"] > div:first-child { padding-top: 1.5rem; }
/* Texto branco SÓ em elementos de texto, NÃO em ícones (Material Symbols) */
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
[data-testid="stSidebar"] p, [data-testid="stSidebar"] label, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: white !important; }
[data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea { color: var(--text) !important; background: rgba(255,255,255,0.95) !important; }
/* Ícones (botão colapsar sidebar) ficam brancos mas mantêm font de ícone */
[data-testid="stSidebar"] [data-testid="stIconMaterial"],
[data-testid="stSidebar"] .material-icons,
[data-testid="stSidebar"] .material-symbols-outlined { color: white !important; }
/* Botão de colapsar a sidebar (canto superior direito da sidebar) — ícone branco */
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapseButton"] * { color: white !important; fill: white !important; }

/* Logo card no topo da sidebar */
.logo-card { background: white; border-radius: 14px; padding: 20px 16px 16px 16px; margin: 0 -4px 20px -4px; box-shadow: 0 6px 20px rgba(0,0,0,0.18); text-align: center; }
.logo-card img { width: 100%; max-width: 180px; height: auto; display: block; margin: 0 auto; }
.logo-card .logo-subtitle { font-size: 11px; color: var(--text-secondary) !important; margin-top: 8px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; }

/* Botões da sidebar com fundo branco semi-transparente */
[data-testid="stSidebar"] .stButton button {
    background: rgba(255,255,255,0.18) !important;
    color: white !important;
    border: 1px solid rgba(255,255,255,0.28) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    backdrop-filter: blur(10px);
    transition: all 0.2s ease;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: rgba(255,255,255,0.28) !important;
    border-color: rgba(255,255,255,0.5) !important;
}

/* Botão ATIVO da sidebar (kind="primary") — efeito afundado/pressionado */
[data-testid="stSidebar"] .stButton button[kind="primary"] {
    background: rgba(0, 0, 0, 0.18) !important;
    color: white !important;
    border: 1px solid rgba(255,255,255,0.45) !important;
    box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.28),
                inset 0 -1px 0 rgba(255,255,255,0.1) !important;
    transform: translateY(1px);
}
[data-testid="stSidebar"] .stButton button[kind="primary"]:hover {
    background: rgba(0, 0, 0, 0.22) !important;
    border-color: rgba(255,255,255,0.6) !important;
}
[data-testid="stSidebar"] .stButton button[kind="primary"]:focus {
    box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.28),
                inset 0 -1px 0 rgba(255,255,255,0.1),
                0 0 0 2px rgba(255,255,255,0.3) !important;
}

/* Info da sidebar — bloco de versão/modelo */
.sidebar-info { background: rgba(255,255,255,0.15); border-radius: 10px; padding: 10px 14px; margin: 6px 0; backdrop-filter: blur(10px); }
.sidebar-info-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px; opacity: 0.85; margin-bottom: 2px; }
.sidebar-info-value { font-size: 13px; font-weight: 600; }

/* ===== TABS ===== */
.stTabs [data-baseweb="tab-list"] { gap: 4px; background: transparent; border-bottom: 1px solid var(--border); padding: 0 4px; }
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: var(--text-secondary) !important;
    border: none !important;
    padding: 12px 20px !important;
    font-weight: 600 !important;
    font-size: 14px !important;
    border-radius: 8px 8px 0 0 !important;
    transition: all 0.2s ease;
}
.stTabs [data-baseweb="tab"]:hover {
    color: var(--text) !important;
    background: rgba(91, 192, 190, 0.04) !important;
}
.stTabs [aria-selected="true"] {
    color: var(--primary-dark) !important;
    background: rgba(91, 192, 190, 0.10) !important;
    box-shadow: inset 0 2px 5px rgba(91, 192, 190, 0.18),
                inset 0 -1px 0 rgba(255, 255, 255, 0.4) !important;
}
/* Sublinhado animado da tab ativa (Streamlit usa um elemento separado) */
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { background-color: var(--primary) !important; }

/* ===== MÉTRICAS NATIVAS (st.metric) ===== */
[data-testid="stMetric"] {
    background: white;
    padding: 20px;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    box-shadow: var(--shadow-sm);
    transition: all 0.2s ease;
}
[data-testid="stMetric"]:hover { box-shadow: var(--shadow-md); }
[data-testid="stMetricValue"] { font-size: 30px !important; font-weight: 700 !important; color: var(--text) !important; }
[data-testid="stMetricLabel"] {
    color: var(--text-secondary) !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
[data-testid="stMetricDelta"] { font-size: 12px !important; font-weight: 500 !important; }

/* ===== CARDS DE MÉTRICA CUSTOMIZADOS ===== */
.metric-card {
    background: white;
    padding: 22px 20px;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    box-shadow: var(--shadow-sm);
    transition: all 0.2s ease;
    height: 100%;
    min-height: 130px;
}
.metric-card:hover { box-shadow: var(--shadow-md); transform: translateY(-1px); }
.metric-card .mc-icon {
    width: 40px; height: 40px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    margin-bottom: 14px; font-size: 20px;
}
.metric-card .mc-value { font-size: 30px; font-weight: 700; color: var(--text); line-height: 1.1; margin: 0; }
.metric-card .mc-label { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.6px; font-weight: 600; margin-top: 6px; }
.metric-card .mc-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }

/* ===== INPUTS ===== */
.stTextInput input, .stTextArea textarea, .stSelectbox > div > div {
    border-radius: var(--radius-sm) !important;
    border-color: var(--border) !important;
    background: white !important;
}
.stTextInput input:focus { border-color: var(--primary) !important; box-shadow: 0 0 0 3px var(--primary-bg) !important; }

/* ===== BOTÕES — área principal ===== */
.main .stButton > button { border-radius: var(--radius-sm) !important; font-weight: 600 !important; transition: all 0.2s ease; }

/* Botões "primary" GLOBAIS (Todas/Mogi/Suzano nas abas, Salvar no Supabase, etc) — força teal */
button[kind="primary"],
button[data-baseweb="button"][kind="primary"] {
    background: var(--primary) !important;
    border-color: var(--primary) !important;
    color: white !important;
}
button[kind="primary"]:hover,
button[data-baseweb="button"][kind="primary"]:hover {
    background: var(--primary-dark) !important;
    border-color: var(--primary-dark) !important;
}
/* Botão "Sair" da sidebar e similares — manter glassmorphism que já existe na regra de sidebar */

/* ===== MENSAGENS DA CONVERSA ===== */
.msg-cliente {
    background: var(--primary-bg);
    border: 1px solid var(--primary-light);
    padding: 10px 14px;
    border-radius: 14px 14px 14px 4px;
    margin: 6px 0;
    max-width: 75%;
    margin-right: auto;
    word-wrap: break-word;
    white-space: pre-wrap;
}
.msg-bia {
    background: white;
    border: 1px solid var(--border);
    padding: 10px 14px;
    border-radius: 14px 14px 4px 14px;
    margin: 6px 0 6px auto;
    max-width: 75%;
    word-wrap: break-word;
    white-space: pre-wrap;
    box-shadow: var(--shadow-sm);
}
.msg-timestamp { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

/* ===== BADGES ===== */
.badge-alerta { background: #fee2e2; color: #991b1b; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-ok { background: #dcfce7; color: #166534; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-info { background: var(--primary-bg); color: var(--primary-dark); padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-amber { background: #fef3c7; color: #92400e; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-purple { background: #ede9fe; color: #5b21b6; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-neutral { background: #f3f4f6; color: #4b5563; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }

/* ===== DIVISORES ===== */
hr { border-color: var(--border-light) !important; margin: 1.5rem 0 !important; }

/* ===== CAPTIONS ===== */
.stCaption, [data-testid="stCaptionContainer"] { color: var(--text-secondary) !important; font-size: 13px !important; }

/* ===== DATAFRAMES / ALERTS ===== */
[data-testid="stDataFrame"] { border-radius: var(--radius) !important; border: 1px solid var(--border) !important; overflow: hidden; }
[data-testid="stAlert"] { border-radius: var(--radius) !important; border: 1px solid var(--border) !important; box-shadow: var(--shadow-sm); }

/* ===== LISTA DE CONVERSAS ===== */
.conv-row { padding: 14px 8px; border-bottom: 1px solid var(--border-light); transition: background 0.15s ease; border-radius: 6px; }
.conv-row:hover { background: var(--primary-bg); }
.conv-name { font-weight: 600; color: var(--text); }
.conv-phone { font-size: 12px; color: var(--text-secondary); }
.conv-meta { font-size: 12px; color: var(--text-muted); }

/* ===== KILL SWITCH (sidebar) ===== */
.kill-switch-box {
    background: rgba(255,255,255,0.15);
    border: 1px solid rgba(255,255,255,0.28);
    border-radius: 10px;
    padding: 12px 14px;
    margin: 6px 0 10px 0;
    backdrop-filter: blur(10px);
}
.kill-switch-box.paused {
    background: rgba(239, 68, 68, 0.32);
    border: 1px solid rgba(255,255,255,0.6);
    animation: kspulse 1.8s ease-in-out infinite;
}
@keyframes kspulse {
    0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.6); }
    50%     { box-shadow: 0 0 0 6px rgba(239,68,68,0); }
}
.kill-switch-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px; opacity: 0.85; margin-bottom: 4px; color: white !important; }
.kill-switch-status { font-size: 15px; font-weight: 700; color: white !important; }
.kill-switch-sub { font-size: 11px; opacity: 0.85; margin-top: 2px; color: white !important; }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# 3.5) HELPERS DE UI — logo e cards
# ============================================================================

@st.cache_data
def _get_logo_html():
    """HTML do logo em base64. Fallback gracioso se arquivo não existir."""
    # Tenta vários caminhos (deploy vs local)
    candidatos = [
        'logo_maislaser.png',
        os.path.join(os.path.dirname(__file__), 'logo_maislaser.png'),
        '/mount/src/dashboard-bia-maislaser/logo_maislaser.png',
    ]
    for caminho in candidatos:
        try:
            with open(caminho, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            return f'<img src="data:image/png;base64,{b64}" alt="Maislaser by Ana Hickmann" />'
        except Exception:
            continue
    # Fallback
    return '<div style="color: var(--text); font-size: 22px; font-weight: 700;">💚 maislaser</div>'


def render_metric_card(icon, value, label, color="primary", sub=None):
    """Card de métrica customizado com ícone colorido."""
    color_map = {
        'primary': '#5BC0BE',
        'green': '#22c55e',
        'red': '#ef4444',
        'amber': '#f59e0b',
        'blue': '#3b82f6',
        'purple': '#8b5cf6',
    }
    cor = color_map.get(color, color)
    sub_html = f'<div class="mc-sub">{sub}</div>' if sub else ''
    return f"""
    <div class="metric-card">
        <div class="mc-icon" style="background: {cor}1A; color: {cor};">{icon}</div>
        <div class="mc-value">{value}</div>
        <div class="mc-label">{label}</div>
        {sub_html}
    </div>
    """


def placeholder_aba(titulo, descricao, etapa_prevista="Próxima entrega"):
    """Renderiza uma aba em construção (estrutura visual antes da implementação)."""
    st.markdown(f"## {titulo}")
    st.markdown(f"""
    <div style="background: white; padding: 40px; border-radius: 12px; border: 2px dashed #E5E7EB; text-align: center; margin: 20px 0;">
        <div style="font-size: 48px; margin-bottom: 16px;">🚧</div>
        <div style="font-size: 18px; font-weight: 600; color: #1A2332; margin-bottom: 8px;">Aba em construção</div>
        <div style="font-size: 14px; color: #6B7280; max-width: 500px; margin: 0 auto 16px auto;">{descricao}</div>
        <div style="display: inline-block; background: var(--primary-bg); color: var(--primary-dark); padding: 6px 14px; border-radius: 999px; font-size: 12px; font-weight: 600;">{etapa_prevista}</div>
    </div>
    """, unsafe_allow_html=True)


# ============================================================================
# 4) AUTENTICAÇÃO SIMPLES
# ============================================================================

def _expected_login_token():
    pw = st.secrets.get("DASHBOARD_PASSWORD", "maislaser")
    salt = "bia_maislaser_v6_persistencia"
    return hashlib.sha256((pw + salt).encode()).hexdigest()[:32]


def check_password():
    qp = st.query_params
    if "t" in qp and qp.get("t") == _expected_login_token():
        st.session_state["password_correct"] = True

    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    st.markdown("""
        <style>
            header[data-testid="stHeader"] {visibility: hidden;}
            section[data-testid="stSidebar"] {display: none;}
            section.main > div.block-container {
                padding-top: 8vh !important;
            }
            .bia-login-header { text-align: center; margin-bottom: 24px; }
            .bia-login-header img { max-width: 200px; height: auto; }
            /* Esconde a label "Senha" — placeholder já indica */
            div[data-testid="stTextInput"] label { display: none; }
            /* Visual de card no form do Streamlit */
            div[data-testid="stForm"] {
                background: white;
                padding: 32px 28px;
                border-radius: 16px;
                box-shadow: 0 8px 28px rgba(91, 192, 190, 0.18);
                border: 1px solid #e5e7eb;
            }
            div[data-testid="stForm"] .stCheckbox {
                margin-top: 4px;
                margin-bottom: 10px;
            }
            /* Botão Entrar com altura confortável */
            div[data-testid="stForm"] button[kind="primary"] {
                height: 44px;
                font-weight: 600;
            }
        </style>
    """, unsafe_allow_html=True)

    # Centralização horizontal via columns — card no terço do meio
    col_esq, col_meio, col_dir = st.columns([1, 1, 1])
    with col_meio:
        # Logo centralizado
        st.markdown(f"""
            <div class="bia-login-header">
                {_get_logo_html()}
            </div>
        """, unsafe_allow_html=True)

        # Form do Streamlit — CSS aplica visual de card
        with st.form("login_form", clear_on_submit=False):
            senha = st.text_input("Senha", type="password", placeholder="Digite a senha")
            lembrar = st.checkbox("Lembrar de mim neste dispositivo", value=True,
                help="Se ativo, você fica logado mesmo depois de fechar o navegador.")
            submit = st.form_submit_button("Entrar", type="primary", use_container_width=True)

        if submit:
            if senha == st.secrets.get("DASHBOARD_PASSWORD", "maislaser"):
                st.session_state["password_correct"] = True
                if lembrar:
                    st.query_params["t"] = _expected_login_token()
                st.rerun()
            else:
                st.error("❌ Senha incorreta")

    return False


# ============================================================================
# 5) CONEXÃO SUPABASE
# ============================================================================

@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# ============================================================================
# 6) CARREGAMENTO DE DADOS
# ============================================================================

@st.cache_data(ttl=20)
def carregar_conversas(dias_atras=7):
    sb = get_supabase()
    data_limite = (datetime.now(TZ_SP) - timedelta(days=dias_atras)).isoformat()
    try:
        result = sb.table("conversas").select("*").gte("criado_em", data_limite).order("criado_em", desc=True).limit(5000).execute()
        df = pd.DataFrame(result.data)
        if not df.empty:
            df['criado_em'] = pd.to_datetime(df['criado_em'], format='ISO8601')
        return df
    except Exception as e:
        st.error(f"Erro ao carregar conversas: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=30)
def carregar_leads():
    sb = get_supabase()
    try:
        result = sb.table("leads").select("*").order("criado_em", desc=True).limit(1000).execute()
        df = pd.DataFrame(result.data)
        if not df.empty:
            df['criado_em'] = pd.to_datetime(df['criado_em'], format='ISO8601')
        return df
    except Exception as e:
        st.error(f"Erro ao carregar leads: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=60)
def carregar_clientes_base_nomes():
    """Carrega só telefone+nome de clientes_base (leve, pra fallback de nome)."""
    sb = get_supabase()
    try:
        result = sb.table("clientes_base").select("telefone, nome").limit(5000).execute()
        df = pd.DataFrame(result.data)
        return df
    except Exception as e:
        # Tabela pode não existir ainda; falha silenciosa
        return pd.DataFrame()


# ─── v5.1 (22/06/2026): NOVO — fallback bia_disparos ─────────────────────────
@st.cache_data(ttl=30)
def carregar_bia_disparos():
    """
    Carrega telefone+nome_indicado+unidade de bia_disparos.
    Usado como fallback pra resolver nome/unidade na aba Conversas — indicados
    do Indique e Ganhe (Bia v5) não entram em `leads` automaticamente, então
    sem esse fallback eles aparecem como 'Sem nome' + '—' na unidade, e os
    filtros Mogi/Suzano não pegam eles.
    """
    sb = get_supabase()
    try:
        result = (sb.table("bia_disparos")
                  .select("telefone, nome_indicado, unidade, nome_cadastrante, status")
                  .limit(5000)
                  .execute())
        df = pd.DataFrame(result.data)
        return df
    except Exception:
        # Tabela pode estar inacessível ou bia_disparos vazio — segue sem fallback
        return pd.DataFrame()


@st.cache_data(ttl=10)
def carregar_configuracoes():
    sb = get_supabase()
    try:
        result = sb.table("configuracoes").select("*").eq("id", 1).execute()
        if result.data:
            return result.data[0]
        return {}
    except Exception as e:
        st.error(f"Erro ao carregar configurações: {e}")
        return {}


def salvar_configuracoes(modo_manutencao,
                          recepcao_mogi_telefone=None, recepcao_mogi_nome=None,
                          recepcao_suzano_telefone=None, recepcao_suzano_nome=None,
                          mogi_telefone=None, mogi_nome=None,
                          suzano_telefone=None, suzano_nome=None):
    """Salva configs no Supabase.

    NOTA: os campos *mogi_telefone/_nome e suzano_telefone/_nome (coordenadoras)
    são legado da Bia v3.x — nenhum workflow da Bia v5 lê eles. Mantidos como
    opcionais pra não dropar a coluna do banco. O UI atual não os preenche mais.
    """
    sb = get_supabase()
    try:
        dados = {
            "id": 1,
            "modo_manutencao": modo_manutencao,
            "atualizado_em": datetime.now(TZ_SP).isoformat(),
        }
        # Coordenadoras (legado v3) — só grava se vier preenchido
        if mogi_telefone is not None:
            dados["mogi_telefone"] = mogi_telefone
        if mogi_nome is not None:
            dados["mogi_nome"] = mogi_nome
        if suzano_telefone is not None:
            dados["suzano_telefone"] = suzano_telefone
        if suzano_nome is not None:
            dados["suzano_nome"] = suzano_nome
        # Recepção (Bia v5)
        if recepcao_mogi_telefone is not None:
            dados["recepcao_mogi_telefone"] = recepcao_mogi_telefone
        if recepcao_mogi_nome is not None:
            dados["recepcao_mogi_nome"] = recepcao_mogi_nome
        if recepcao_suzano_telefone is not None:
            dados["recepcao_suzano_telefone"] = recepcao_suzano_telefone
        if recepcao_suzano_nome is not None:
            dados["recepcao_suzano_nome"] = recepcao_suzano_nome

        sb.table("configuracoes").upsert(dados).execute()
        carregar_configuracoes.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")
        return False


def set_modo_manutencao(novo_estado: bool) -> bool:
    """Toggle do kill switch — altera SÓ o campo modo_manutencao.

    Usado pelo botão da sidebar. Mais leve que salvar_configuracoes (que faz
    upsert com vários campos). UPDATE direto, sem mexer em coordenadora/recepção.
    """
    sb = get_supabase()
    try:
        sb.table("configuracoes").update({
            "modo_manutencao": novo_estado,
            "atualizado_em": datetime.now(TZ_SP).isoformat(),
        }).eq("id", 1).execute()
        carregar_configuracoes.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao alterar modo manutenção: {e}")
        return False


@st.cache_data(ttl=30)
def carregar_agendamentos():
    sb = get_supabase()
    try:
        result = sb.table("agendamentos").select("*").order("data_hora", desc=True).limit(500).execute()
        df = pd.DataFrame(result.data)
        if not df.empty:
            df['data_hora'] = pd.to_datetime(df['data_hora'], format='ISO8601')
            df['criado_em'] = pd.to_datetime(df['criado_em'], format='ISO8601')
        return df
    except Exception as e:
        st.error(f"Erro ao carregar agendamentos: {e}")
        return pd.DataFrame()


# ============================================================================
# 7) HELPERS
# ============================================================================

def agrupar_conversas(df_conv, df_leads, df_agend=None, df_clientes_base=None, df_bia_disparos=None):
    """
    Agrupa conversas por telefone e resolve nome/unidade com FALLBACK:
      - nome: leads.nome → agendamentos.nome → clientes_base.nome → bia_disparos.nome_indicado
      - unidade: leads.slot_unidade → leads.unidade → agendamentos.unidade → bia_disparos.unidade

    v5.1 (22/06/2026): adiciona bia_disparos como 4º fallback. Antes os
    indicados do Indique e Ganhe apareciam como "Sem nome" + "—" e ficavam de
    fora dos filtros Mogi/Suzano.
    """
    if df_conv.empty:
        return pd.DataFrame()

    df_conv_sorted = df_conv.sort_values('criado_em', ascending=False)
    grouped = df_conv_sorted.groupby('telefone').agg(
        ultima_mensagem=('mensagem', 'first'),
        ultimo_papel=('papel', 'first'),
        ultima_atualizacao=('criado_em', 'first'),
        primeira_atualizacao=('criado_em', 'last'),
        total_mensagens=('id', 'count'),
        total_tokens=('tokens', 'sum'),
    ).reset_index()

    grouped['ultima_mensagem_preview'] = grouped['ultima_mensagem'].apply(
        lambda x: (x[:80] + '...') if isinstance(x, str) and len(x) > 80 else x
    )

    # ─── Merge com leads (nome, unidade, slot_unidade, etc) ───
    if not df_leads.empty:
        cols_lead = ['telefone', 'nome', 'unidade', 'status', 'tipo_cliente', 'genero']
        if 'slot_unidade' in df_leads.columns:
            cols_lead.append('slot_unidade')
        if 'transferido_em' in df_leads.columns:
            cols_lead.append('transferido_em')
        grouped = grouped.merge(df_leads[cols_lead], on='telefone', how='left')
    else:
        grouped['nome'] = None
        grouped['unidade'] = None
        grouped['slot_unidade'] = None
        grouped['status'] = None
        grouped['tipo_cliente'] = None
        grouped['genero'] = None

    # ─── FALLBACK DE NOME ───
    # 1. leads.nome (já no merge)
    # 2. agendamentos.nome
    if df_agend is not None and not df_agend.empty and 'nome' in df_agend.columns:
        nomes_agend = (df_agend[['telefone', 'nome']]
                       .dropna(subset=['nome'])
                       .drop_duplicates('telefone', keep='first')
                       .rename(columns={'nome': '_nome_agend'}))
        grouped = grouped.merge(nomes_agend, on='telefone', how='left')
        grouped['nome'] = grouped['nome'].fillna(grouped['_nome_agend'])
        grouped = grouped.drop(columns=['_nome_agend'], errors='ignore')
    # 3. clientes_base.nome
    if df_clientes_base is not None and not df_clientes_base.empty and 'nome' in df_clientes_base.columns:
        nomes_cb = (df_clientes_base[['telefone', 'nome']]
                    .dropna(subset=['nome'])
                    .drop_duplicates('telefone', keep='first')
                    .rename(columns={'nome': '_nome_cb'}))
        grouped = grouped.merge(nomes_cb, on='telefone', how='left')
        grouped['nome'] = grouped['nome'].fillna(grouped['_nome_cb'])
        grouped = grouped.drop(columns=['_nome_cb'], errors='ignore')
    # 4. bia_disparos.nome_indicado (indicados do Indique e Ganhe / Bia v5) — v5.1
    if df_bia_disparos is not None and not df_bia_disparos.empty and 'nome_indicado' in df_bia_disparos.columns:
        nomes_bd = (df_bia_disparos[['telefone', 'nome_indicado']]
                    .dropna(subset=['nome_indicado'])
                    .drop_duplicates('telefone', keep='first')
                    .rename(columns={'nome_indicado': '_nome_bd'}))
        grouped = grouped.merge(nomes_bd, on='telefone', how='left')
        grouped['nome'] = grouped['nome'].fillna(grouped['_nome_bd'])
        grouped = grouped.drop(columns=['_nome_bd'], errors='ignore')

    # ─── FALLBACK DE UNIDADE ───
    # 1. slot_unidade (confirmado pelo cliente, mais confiável)
    # 2. unidade do lead (operacional/fallback)
    # 3. unidade do agendamento mais recente
    # 4. unidade da campanha bia_disparos — v5.1
    if 'slot_unidade' in grouped.columns:
        grouped['_unidade_resolvida'] = grouped['slot_unidade'].fillna(grouped['unidade'])
    else:
        grouped['_unidade_resolvida'] = grouped['unidade']

    if df_agend is not None and not df_agend.empty and 'unidade' in df_agend.columns:
        agend_sorted = df_agend.sort_values('criado_em', ascending=False) if 'criado_em' in df_agend.columns else df_agend
        unid_agend = (agend_sorted[['telefone', 'unidade']]
                      .dropna(subset=['unidade'])
                      .drop_duplicates('telefone', keep='first')
                      .rename(columns={'unidade': '_unid_agend'}))
        grouped = grouped.merge(unid_agend, on='telefone', how='left')
        grouped['_unidade_resolvida'] = grouped['_unidade_resolvida'].fillna(grouped['_unid_agend'])
        grouped = grouped.drop(columns=['_unid_agend'], errors='ignore')

    # v5.1: bia_disparos.unidade (indicados do Indique e Ganhe)
    if df_bia_disparos is not None and not df_bia_disparos.empty and 'unidade' in df_bia_disparos.columns:
        unid_bd = (df_bia_disparos[['telefone', 'unidade']]
                   .dropna(subset=['unidade'])
                   .drop_duplicates('telefone', keep='first')
                   .rename(columns={'unidade': '_unid_bd'}))
        grouped = grouped.merge(unid_bd, on='telefone', how='left')
        grouped['_unidade_resolvida'] = grouped['_unidade_resolvida'].fillna(grouped['_unid_bd'])
        grouped = grouped.drop(columns=['_unid_bd'], errors='ignore')

    grouped['unidade'] = grouped['_unidade_resolvida']
    grouped = grouped.drop(columns=['_unidade_resolvida'], errors='ignore')

    grouped['alertas'] = grouped.apply(
        lambda row: detectar_alertas(row, df_conv, df_agend, df_leads),
        axis=1
    )

    return grouped.sort_values('ultima_atualizacao', ascending=False)


def detectar_alertas(row, df_conv, df_agend=None, df_leads_full=None):
    alertas = []
    telefone = row['telefone']
    msgs_dessa_conv = df_conv[df_conv['telefone'] == telefone].sort_values('criado_em').reset_index(drop=True)

    if msgs_dessa_conv.empty:
        return alertas

    msgs_bia = msgs_dessa_conv[msgs_dessa_conv['papel'] == 'assistant']['mensagem'].str.lower().fillna('')
    msgs_user = msgs_dessa_conv[msgs_dessa_conv['papel'] == 'user']['mensagem'].str.lower().fillna('')
    todas_bia = ' '.join(msgs_bia.tolist())
    todas_user = ' '.join(msgs_user.tolist())

    tem_agendamento = False
    if df_agend is not None and not df_agend.empty and 'telefone' in df_agend.columns:
        tem_agendamento = (df_agend['telefone'].astype(str) == str(telefone)).any()

    tem_transferencia = False
    if df_leads_full is not None and not df_leads_full.empty and 'transferido_em' in df_leads_full.columns:
        match = df_leads_full[df_leads_full['telefone'].astype(str) == str(telefone)]
        if not match.empty:
            tem_transferencia = pd.notna(match.iloc[0]['transferido_em'])

    sinais_cliente_existente = ['já sou cliente', 'ja sou cliente', 'já faço aí', 'perdi a sessão',
                                'perdi minha sessão', 'quero reagendar', 'quero remarcar', 'já fiz aí']
    cliente_se_identificou = any(s in todas_user for s in sinais_cliente_existente)

    if cliente_se_identificou:
        ts_identificacao = None
        for _, msg in msgs_dessa_conv.iterrows():
            if msg['papel'] == 'user' and any(s in str(msg['mensagem']).lower() for s in sinais_cliente_existente):
                ts_identificacao = msg['criado_em']
                break

        if ts_identificacao is not None:
            msgs_bia_depois = msgs_dessa_conv[
                (msgs_dessa_conv['criado_em'] > ts_identificacao) &
                (msgs_dessa_conv['papel'] == 'assistant')
            ]['mensagem'].str.lower().fillna('').tolist()

            if any('presente' in m or 'ganhou' in m or '5 sessões' in m or 'cortesia' in m for m in msgs_bia_depois):
                alertas.append('🔴 Falou de presente pra cliente existente')

    if row['total_mensagens'] > 8 and not tem_agendamento and not tem_transferencia:
        if 'encerrar' not in todas_bia:
            alertas.append('🟡 Conversa longa sem desfecho')

    if any(p in todas_user for p in ['quanto custa', 'qual o preço', 'qual o valor', 'parcelamento', 'desconto']):
        if not tem_transferencia and 'coordenadora' not in todas_bia:
            alertas.append('🟠 Preço sem transferência')

    return alertas


def formatar_conversa_para_copiar(msgs_df, nome_cliente="Cliente"):
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


def detectar_tags(mensagem):
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
# 8) RENDER DETALHE
# ============================================================================

def renderizar_conversa(telefone, df_conv, df_leads, df_agend=None, df_clientes_base=None, df_bia_disparos=None):
    """
    v5.1 (22/06/2026): aceita df_bia_disparos como 4º fallback de nome/unidade.
    """
    msgs = df_conv[df_conv['telefone'] == telefone].sort_values('criado_em')

    if msgs.empty:
        st.warning("Conversa não encontrada.")
        return

    # Resolver nome com fallback (mesma lógica de agrupar_conversas)
    nome = "Sem nome"
    lead = df_leads[df_leads['telefone'] == telefone] if not df_leads.empty else pd.DataFrame()
    if not lead.empty and pd.notna(lead.iloc[0].get('nome')):
        nome = lead.iloc[0]['nome']
    elif df_agend is not None and not df_agend.empty:
        match_a = df_agend[df_agend['telefone'] == telefone]
        if not match_a.empty and pd.notna(match_a.iloc[0].get('nome')):
            nome = match_a.iloc[0]['nome']
    if nome == "Sem nome" and df_clientes_base is not None and not df_clientes_base.empty:
        match_c = df_clientes_base[df_clientes_base['telefone'] == telefone]
        if not match_c.empty and pd.notna(match_c.iloc[0].get('nome')):
            nome = match_c.iloc[0]['nome']
    # v5.1: fallback bia_disparos.nome_indicado
    if nome == "Sem nome" and df_bia_disparos is not None and not df_bia_disparos.empty:
        match_bd = df_bia_disparos[df_bia_disparos['telefone'] == telefone]
        if not match_bd.empty and pd.notna(match_bd.iloc[0].get('nome_indicado')):
            nome = match_bd.iloc[0]['nome_indicado']

    # Resolver unidade com fallback
    unidade = '-'
    if not lead.empty:
        slot_u = lead.iloc[0].get('slot_unidade') if 'slot_unidade' in lead.columns else None
        unid_l = lead.iloc[0].get('unidade')
        unidade = slot_u or unid_l or unidade
    if (unidade == '-' or pd.isna(unidade)) and df_agend is not None and not df_agend.empty:
        match_a = df_agend[df_agend['telefone'] == telefone]
        if not match_a.empty and pd.notna(match_a.iloc[0].get('unidade')):
            unidade = match_a.iloc[0]['unidade']
    # v5.1: fallback bia_disparos.unidade
    if (unidade == '-' or pd.isna(unidade)) and df_bia_disparos is not None and not df_bia_disparos.empty:
        match_bd = df_bia_disparos[df_bia_disparos['telefone'] == telefone]
        if not match_bd.empty and pd.notna(match_bd.iloc[0].get('unidade')):
            unidade = match_bd.iloc[0]['unidade']

    col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
    with col1:
        st.markdown(f"### 💬 {nome}")
        st.caption(f"📱 +{telefone}")
    with col2:
        st.metric("Unidade", str(unidade).title() if unidade and unidade != '-' else '—')
    with col3:
        if not lead.empty:
            tipo = lead.iloc[0].get('tipo_cliente') or 'novo'
            st.metric("Tipo", str(tipo).title())
        else:
            st.metric("Tipo", "Novo")
    with col4:
        st.metric("Mensagens", len(msgs))

    col_btn1, col_btn2, col_btn3 = st.columns([2, 2, 6])
    with col_btn1:
        conversa_formatada = formatar_conversa_para_copiar(msgs, nome)
        st.download_button("📥 Baixar conversa (.txt)", data=conversa_formatada,
            file_name=f"conversa_{telefone}.txt", mime="text/plain", use_container_width=True)
    with col_btn2:
        st.link_button("🔗 Abrir WhatsApp", url=f"https://wa.me/{telefone}", use_container_width=True)

    with st.expander("📋 **Copiar conversa formatada** (pra colar no Claude)", expanded=False):
        st.code(conversa_formatada, language="text")
        st.caption("Clique no ícone de cópia no canto superior direito do bloco acima.")

    st.divider()

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
# 9) TELAS DAS ABAS
# ============================================================================

# ─────────── ABA 1: 💬 CONVERSAS (modernizada) ───────────

def tela_conversas(df_conv, df_leads, df_agend, df_clientes_base=None, df_bia_disparos=None):
    """
    v5.1 (22/06/2026): passa df_bia_disparos pra agrupar_conversas.
    """
    st.markdown("## 💬 Conversas")
    st.caption("Acompanhe em tempo real o que a Bia tá conversando.")

    col_filt1, col_filt2, col_filt3, col_filt4 = st.columns([2, 2, 2, 3])
    with col_filt1:
        periodo = st.selectbox("Período", ["Hoje", "Ontem", "Últimos 7 dias", "Últimos 30 dias"], index=2, key="filt_periodo")
    with col_filt2:
        unidade_filt = st.selectbox("Unidade", ["Todas", "Mogi", "Suzano"], key="filt_unidade")
    with col_filt3:
        so_alertas = st.checkbox("⚠️ Só com alertas", key="filt_alertas")
    with col_filt4:
        busca = st.text_input("🔎 Buscar (telefone ou nome)", key="filt_busca")

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

    df_agrupado = agrupar_conversas(df_conv, df_leads, df_agend, df_clientes_base, df_bia_disparos)

    if df_agrupado.empty:
        st.info("Nenhuma conversa no período selecionado.")
        return

    if unidade_filt != "Todas":
        df_agrupado = df_agrupado[df_agrupado['unidade'].astype(str).str.contains(unidade_filt.lower(), case=False, na=False)]
    if so_alertas:
        df_agrupado = df_agrupado[df_agrupado['alertas'].apply(lambda x: len(x) > 0)]
    if busca:
        busca_lower = busca.lower()
        df_agrupado = df_agrupado[
            df_agrupado['telefone'].str.contains(busca_lower, case=False, na=False) |
            df_agrupado['nome'].fillna('').str.contains(busca_lower, case=False, na=False)
        ]

    # ───── CARDS MODERNOS PARA OS KPIs ─────
    qtd_conversas = len(df_agrupado)
    qtd_msgs = int(df_agrupado['total_mensagens'].sum()) if not df_agrupado.empty else 0
    qtd_alertas = int(df_agrupado['alertas'].apply(lambda x: len(x) > 0).sum())
    tokens_total = int(df_agrupado['total_tokens'].sum()) if not df_agrupado.empty else 0
    custo = (tokens_total / 1_000_000) * CUSTO_USD_POR_MTOK

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1:
        st.markdown(render_metric_card("💬", qtd_conversas, "Conversas", "primary"), unsafe_allow_html=True)
    with col_m2:
        st.markdown(render_metric_card("📨", qtd_msgs, "Mensagens", "blue"), unsafe_allow_html=True)
    with col_m3:
        cor_al = "red" if qtd_alertas > 0 else "green"
        st.markdown(render_metric_card("⚠️", qtd_alertas, "Com alertas", cor_al), unsafe_allow_html=True)
    with col_m4:
        st.markdown(render_metric_card("💰", f"US$ {custo:.3f}", "Custo IA", "amber",
                                       sub=f"{tokens_total:,} tokens".replace(',', '.')), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    if df_agrupado.empty:
        st.info("Nada por aqui com esses filtros.")
        return

    st.markdown("### Lista de conversas")
    st.caption(f"📊 {len(df_agrupado)} conversa(s) · clique em **Ver** pra abrir aqui, ou **🔗** pra abrir em nova aba")

    h1, h2, h3, h4, h5, h6, h7 = st.columns([2, 1.5, 1, 2.8, 1, 0.9, 0.7])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Última msg**")
    h5.markdown("**Quando**")
    h6.markdown("**Ação**")
    h7.markdown("**🔗**")
    st.markdown('<hr style="margin: 4px 0 8px 0;">', unsafe_allow_html=True)

    # v5.2: monta token de login pra preservar na URL da nova aba
    _qp_now = st.query_params
    _token_param = f"t={_qp_now['t']}&" if "t" in _qp_now else ""

    for idx, row in df_agrupado.head(50).iterrows():
        c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 1.5, 1, 2.8, 1, 0.9, 0.7])

        nome_display = row['nome'] if pd.notna(row.get('nome')) else "Sem nome"
        c1.markdown(f"<div class='conv-name'>{nome_display}</div>", unsafe_allow_html=True)
        if row['alertas']:
            for a in row['alertas']:
                c1.markdown(f'<span class="badge-alerta">{a}</span>', unsafe_allow_html=True)

        c2.markdown(f"<div class='conv-phone'>+{row['telefone']}</div>", unsafe_allow_html=True)
        unidade_str = str(row['unidade']).title() if pd.notna(row.get('unidade')) and row.get('unidade') else '—'
        c3.markdown(f"<div class='conv-meta'>{unidade_str}</div>", unsafe_allow_html=True)

        papel_emoji = "👤" if row['ultimo_papel'] == 'user' else "💚"
        c4.markdown(f"<div class='conv-meta'>{papel_emoji} {row['ultima_mensagem_preview']}</div>", unsafe_allow_html=True)

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
        c5.markdown(f"<div class='conv-meta'>{tempo_str}</div>", unsafe_allow_html=True)

        if c6.button("Ver", key=f"btn_{row['telefone']}_{idx}"):
            st.session_state['conversa_selecionada'] = row['telefone']
            st.rerun()
        # v5.2: botão "🔗" abre conversa em nova guia (link_button do Streamlit
        # abre em _blank por padrão). Preserva o token de login na URL.
        c7.link_button(
            "🔗",
            url=f"?{_token_param}conversa={row['telefone']}",
            help="Abrir em nova guia",
        )

    if len(df_agrupado) > 50:
        st.caption(f"Mostrando 50 de {len(df_agrupado)} conversas. Use os filtros pra refinar.")


# ─────────── ABA 3: 📅 AGENDAMENTOS (status derivado dos campos reais) ───────────

def _derivar_status_agendamento(row):
    """
    Status real do agendamento, derivado dos campos preenchidos pelos botões
    dos templates D-1 (CONFIRMAR/REAGENDAR) e do dia (ESTOU INDO/NÃO VOU).

    Prioridade (decrescente): NÃO VOU > REAGENDAR > ESTOU INDO > CONFIRMADO >
    status do banco (realizado/cancelado/faltou) > agendado pendente.

    Retorna: (chave, texto_badge, classe_css)
    """
    # 1) Cliente disse que NÃO VAI
    if pd.notna(row.get('nao_vou_conseguir_em')):
        return ('nao_vai', '❌ Não vai', 'badge-alerta')

    # 2) Cliente pediu pra REAGENDAR
    if pd.notna(row.get('reagendamento_solicitado_em')):
        return ('reagendar', '🔄 Pediu reagendar', 'badge-amber')

    # 3) Cliente disse que está A CAMINHO (botão "ESTOU INDO" do template do dia)
    if pd.notna(row.get('estou_indo_em')):
        return ('a_caminho', '🚗 A caminho', 'badge-ok')

    # 4) Cliente CONFIRMOU presença (botão "CONFIRMAR PRESENÇA" do D-1)
    status_banco = str(row.get('status') or 'agendado').lower()
    if pd.notna(row.get('confirmado_em')) or status_banco == 'confirmado':
        return ('confirmado', '✅ Confirmado', 'badge-ok')

    # 5+) Status terminal vindo do banco
    if status_banco == 'realizado':
        return ('realizado', '🎉 Realizado', 'badge-ok')
    if status_banco == 'cancelado':
        return ('cancelado', '❌ Cancelado', 'badge-alerta')
    if status_banco in ('faltou', 'no_show'):
        return ('faltou', '😶 Faltou', 'badge-amber')

    # default: agendado pendente (nenhum botão clicado ainda)
    return ('pendente', '⏳ Pendente', 'badge-info')


def tela_agendamentos(df_agend, df_leads, df_conv):
    st.markdown("# 📅 Agendamentos")
    st.caption("Sessões de cortesia agendadas pela Bia")

    if df_agend is None or df_agend.empty:
        st.info("📭 Nenhum agendamento registrado ainda. Quando a Bia agendar a primeira cortesia, ela aparecerá aqui.")
        return

    df = df_agend.copy()

    try:
        df['data_hora_sp'] = df['data_hora'].dt.tz_convert(TZ_SP)
    except Exception:
        df['data_hora_sp'] = df['data_hora']

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

    # ─── Status derivado (combina status do banco com campos confirmado_em,
    # estou_indo_em, reagendamento_solicitado_em, nao_vou_conseguir_em) ───
    df['_status_chave'] = df.apply(lambda r: _derivar_status_agendamento(r)[0], axis=1)
    df['_status_texto'] = df.apply(lambda r: _derivar_status_agendamento(r)[1], axis=1)
    df['_status_classe'] = df.apply(lambda r: _derivar_status_agendamento(r)[2], axis=1)

    if 'agend_unidade_btn' not in st.session_state:
        st.session_state['agend_unidade_btn'] = "Todas"

    cnt_todas = len(df)
    cnt_mogi = int((df['unidade_norm'] == 'Mogi').sum())
    cnt_suzano = int((df['unidade_norm'] == 'Suzano').sum())

    btn_col1, btn_col2, btn_col3, _ = st.columns([1.2, 1.4, 1.2, 4])

    with btn_col1:
        is_todas = st.session_state['agend_unidade_btn'] == "Todas"
        if st.button(f"🏢 Todas ({cnt_todas})", type="primary" if is_todas else "secondary",
                     use_container_width=True, key="btn_agend_todas"):
            st.session_state['agend_unidade_btn'] = "Todas"
            st.rerun()

    with btn_col2:
        is_mogi = st.session_state['agend_unidade_btn'] == "Mogi"
        if st.button(f"📍 Mogi ({cnt_mogi})", type="primary" if is_mogi else "secondary",
                     use_container_width=True, key="btn_agend_mogi"):
            st.session_state['agend_unidade_btn'] = "Mogi"
            st.rerun()

    with btn_col3:
        is_suzano = st.session_state['agend_unidade_btn'] == "Suzano"
        if st.button(f"📍 Suzano ({cnt_suzano})", type="primary" if is_suzano else "secondary",
                     use_container_width=True, key="btn_agend_suzano"):
            st.session_state['agend_unidade_btn'] = "Suzano"
            st.rerun()

    unidade_filtro = st.session_state['agend_unidade_btn']

    st.markdown("")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        periodo = st.selectbox("Período",
            ["Próximos (hoje em diante)", "Hoje", "Próximos 7 dias", "Últimos 30 dias", "Tudo"],
            index=0, key="agend_periodo")
    with col_f2:
        # Opções de status agora vêm do status DERIVADO, não do banco
        opcoes_status_derivado = [
            "Todos",
            "⏳ Pendente",
            "✅ Confirmado",
            "🚗 A caminho",
            "🔄 Pediu reagendar",
            "❌ Não vai",
            "🎉 Realizado",
            "❌ Cancelado",
            "😶 Faltou",
        ]
        status_filtro = st.selectbox("Status", opcoes_status_derivado, key="agend_status")

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

    # Filtro por status DERIVADO
    if status_filtro != "Todos":
        df_filt = df_filt[df_filt['_status_texto'] == status_filtro]

    if unidade_filtro != "Todas":
        df_filt = df_filt[df_filt['unidade_norm'] == unidade_filtro]

    if periodo in ["Próximos (hoje em diante)", "Hoje", "Próximos 7 dias"]:
        df_filt = df_filt.sort_values('data_hora', ascending=True)
    else:
        df_filt = df_filt.sort_values('data_hora', ascending=False)

    st.divider()

    # ─── Métricas BASEADAS NO STATUS DERIVADO ───
    chaves = df_filt['_status_chave'] if not df_filt.empty else pd.Series(dtype=str)

    # "Confirmados" = cliente respondeu de qualquer jeito positivo
    # (confirmou D-1, disse que está indo, ou ja foi marcado realizado)
    confirmados = int(chaves.isin(['confirmado', 'a_caminho', 'realizado']).sum())

    # "Pendente confirmar" = status='agendado' sem nenhum botão clicado
    pendentes = int((chaves == 'pendente').sum())

    # "Cancelados/Reagendar" = cliente pediu reagendar ou disse que não vai
    cancelados = int(chaves.isin(['nao_vai', 'reagendar', 'cancelado']).sum())

    try:
        proximos_7 = int(((df_filt['data_hora_sp'] >= hoje_inicio) &
                          (df_filt['data_hora_sp'] <= agora + timedelta(days=7))).sum())
    except Exception:
        proximos_7 = 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(render_metric_card("📅", len(df_filt), "Total no filtro", "primary"), unsafe_allow_html=True)
    with col2:
        st.markdown(render_metric_card("✅", confirmados, "Confirmados", "green",
                                       sub="confirmou D-1 ou disse que tá indo"), unsafe_allow_html=True)
    with col3:
        cor_pend = "red" if pendentes > 0 else "amber"
        st.markdown(render_metric_card("⏳", pendentes, "Pendente confirmar", cor_pend), unsafe_allow_html=True)
    with col4:
        cor_canc = "red" if cancelados > 0 else "blue"
        st.markdown(render_metric_card("🔄", cancelados, "Reagendar/Cancelar", cor_canc,
                                       sub="precisam de atenção da recepção"), unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown(f"### 📋 Lista — {len(df_filt)} agendamento(s)")

    if df_filt.empty:
        st.info("Nenhum agendamento com esses filtros.")
        return

    h1, h2, h3, h4, h5, h6, h7 = st.columns([1.6, 1.4, 0.9, 1.2, 1.4, 1.3, 0.9])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Área**")
    h5.markdown("**Quando**")
    h6.markdown("**Status**")
    h7.markdown("**Ação**")
    st.divider()

    for _, ag in df_filt.iterrows():
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.6, 1.4, 0.9, 1.2, 1.4, 1.3, 0.9])

        nome = ag.get('nome') or '—'
        telefone = str(ag.get('telefone', '—'))
        unidade = ag.get('unidade_norm') or '—'
        area = ag.get('area') or '—'
        fazer = bool(ag.get('fazer_na_hora', False))

        try:
            quando = ag['data_hora_sp'].strftime('%d/%m %H:%M')
        except Exception:
            quando = '—'

        is_futuro = False
        try:
            is_futuro = ag['data_hora_sp'] >= agora
        except Exception:
            pass
        prefixo = "⏭️ " if is_futuro else ""

        c1.write(f"{prefixo}{nome}")
        c2.write(f"+{telefone}" if not telefone.startswith('+') else telefone)
        c3.write(unidade)
        if fazer:
            c4.markdown(f"{area} <span class='badge-amber'>⚡ na hora</span>", unsafe_allow_html=True)
        else:
            c4.write(area)
        c5.write(quando)

        # Badge usando o STATUS DERIVADO
        c6.markdown(f"<span class='{ag['_status_classe']}'>{ag['_status_texto']}</span>",
                    unsafe_allow_html=True)

        if c7.button("Ver", key=f"ver_agend_{telefone}_{ag.name}"):
            st.session_state['conversa_selecionada'] = telefone
            st.rerun()

        st.markdown("---")

    st.caption("⏭️ = agendamento futuro  ·  badges coloridos refletem a última resposta do cliente aos templates de lembrete")


# ─────────── ABA 4: 📈 MÉTRICAS (mantida, fix de funil vem na Entrega 2) ───────────

def tela_metricas(df_conv, df_leads, df_agend, df_bia_disparos=None):
    """
    v5.1 (22/06/2026): passa df_bia_disparos pra agrupar_conversas no bloco
    de conversas problemáticas.
    """
    st.markdown("## 📈 Métricas")

    periodo = st.selectbox("Período de análise", ["Hoje", "Últimos 7 dias", "Últimos 30 dias"], index=1, key="met_periodo")

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

    st.markdown("### Resumo do período")
    conversas_unicas = df_conv_p['telefone'].nunique() if not df_conv_p.empty and 'telefone' in df_conv_p.columns else 0
    total_msgs = len(df_conv_p)
    msgs_bia = len(df_conv_p[df_conv_p['papel'] == 'assistant']) if not df_conv_p.empty and 'papel' in df_conv_p.columns else 0
    tokens_total = int(df_conv_p['tokens'].sum()) if not df_conv_p.empty and 'tokens' in df_conv_p.columns else 0
    custo_usd = (tokens_total / 1_000_000) * CUSTO_USD_POR_MTOK
    custo_brl = custo_usd * 5.50

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(render_metric_card("💬", conversas_unicas, "Conversas únicas", "primary"), unsafe_allow_html=True)
    with c2:
        st.markdown(render_metric_card("📨", total_msgs, "Mensagens totais", "blue"), unsafe_allow_html=True)
    with c3:
        st.markdown(render_metric_card("💚", msgs_bia, "Respostas Bia", "green"), unsafe_allow_html=True)
    with c4:
        tokens_fmt = f"{tokens_total:,}".replace(',', '.')
        st.markdown(render_metric_card("💰", f"R$ {custo_brl:.2f}", "Custo IA", "amber",
                                       sub=f"{tokens_fmt} tokens · US$ {custo_usd:.4f}"), unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    st.divider()
    st.markdown("### 🎯 Funil de conversão")
    st.caption("Cruza conversas do período com as tabelas reais de agendamentos e transferências.")

    if df_conv_p.empty:
        st.info("Sem dados no período.")
    else:
        iniciaram = conversas_unicas
        if not df_conv_p.empty and 'telefone' in df_conv_p.columns:
            msgs_por_tel = df_conv_p.groupby('telefone').size()
            engajaram = int((msgs_por_tel >= 3).sum())
        else:
            engajaram = 0

        # ─── Lookup REAL: telefones que estão no df de conversas DO PERÍODO ───
        telefones_no_periodo = set(df_conv_p['telefone'].astype(str).unique()) if not df_conv_p.empty else set()

        # Agendaram: cruzamento com tabela agendamentos (criados no período)
        telefones_agendaram = set()
        if df_agend is not None and not df_agend.empty and 'telefone' in df_agend.columns:
            if 'criado_em' in df_agend.columns:
                df_ag_per = df_agend[df_agend['criado_em'] >= dt_inicio]
            else:
                df_ag_per = df_agend
            telefones_agendaram = set(df_ag_per['telefone'].astype(str)) & telefones_no_periodo
        agendaram = len(telefones_agendaram)

        # Transferiram: cruzamento com leads.transferido_em (no período)
        telefones_transferiram = set()
        if not df_leads.empty and 'transferido_em' in df_leads.columns:
            df_t = df_leads[df_leads['transferido_em'].notna()].copy()
            if not df_t.empty:
                df_t['transferido_em'] = pd.to_datetime(df_t['transferido_em'], errors='coerce')
                df_t = df_t[df_t['transferido_em'] >= dt_inicio]
                telefones_transferiram = set(df_t['telefone'].astype(str)) & telefones_no_periodo
        transferiram = len(telefones_transferiram)

        fig = go.Figure(go.Funnel(
            y=["Iniciaram conversa", "Engajaram (3+ msgs)", "Transferiram", "Agendaram"],
            x=[iniciaram, engajaram, transferiram, agendaram],
            textinfo="value+percent initial",
            marker={"color": [COR_PRIMARIA, COR_PRIMARIA_DARK, "#15803d", "#14532d"]}
        ))
        fig.update_layout(height=350, margin=dict(t=20, b=20, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

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
            df_leads_p = df_leads[df_leads['criado_em'] >= dt_inicio].copy()
            if not df_leads_p.empty:
                # Fallback de unidade: slot_unidade > unidade > agendamentos.unidade
                if 'slot_unidade' in df_leads_p.columns:
                    df_leads_p['_unidade_resolvida'] = df_leads_p['slot_unidade'].fillna(df_leads_p['unidade'])
                else:
                    df_leads_p['_unidade_resolvida'] = df_leads_p['unidade']

                if df_agend is not None and not df_agend.empty and 'unidade' in df_agend.columns:
                    unid_ag = (df_agend[['telefone', 'unidade']]
                               .dropna(subset=['unidade'])
                               .drop_duplicates('telefone', keep='first')
                               .rename(columns={'unidade': '_unid_ag'}))
                    df_leads_p = df_leads_p.merge(unid_ag, on='telefone', how='left')
                    df_leads_p['_unidade_resolvida'] = df_leads_p['_unidade_resolvida'].fillna(df_leads_p['_unid_ag'])

                # Normalizar pra Mogi das Cruzes / Suzano / Desconhecido
                def _norm_unidade(u):
                    if not isinstance(u, str) or not u.strip():
                        return 'Desconhecida'
                    ul = u.lower().strip()
                    if 'mogi' in ul or 'monte' in ul:
                        return 'Mogi das Cruzes'
                    elif 'suzano' in ul:
                        return 'Suzano'
                    return u.title()

                df_leads_p['_unidade_norm'] = df_leads_p['_unidade_resolvida'].apply(_norm_unidade)
                unidade_count = df_leads_p['_unidade_norm'].value_counts().reset_index()
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
    st.markdown("### 💰 Custo IA por dia")
    if not df_conv_p.empty:
        df_copia = df_conv_p.copy()
        df_copia['dia'] = df_copia['criado_em'].dt.tz_convert(TZ_SP).dt.date
        custo_dia = df_copia.groupby('dia')['tokens'].sum().reset_index()
        custo_dia['Custo USD'] = (custo_dia['tokens'] / 1_000_000) * CUSTO_USD_POR_MTOK
        custo_dia['Custo BRL'] = custo_dia['Custo USD'] * 5.50
        custo_dia.columns = ['Dia', 'Tokens', 'Custo USD', 'Custo BRL']
        # Formatar 'Dia' como string DD/MM pra evitar eixo X com timestamps esquisitos
        custo_dia['Dia'] = pd.to_datetime(custo_dia['Dia']).dt.strftime('%d/%m')
        # 1 ponto → bar chart fica melhor; vários → linha com marcadores
        if len(custo_dia) == 1:
            fig = px.bar(custo_dia, x='Dia', y='Custo BRL', color_discrete_sequence=[COR_PRIMARIA],
                         text='Custo BRL')
            fig.update_traces(texttemplate='R$ %{text:.2f}', textposition='outside')
        else:
            fig = px.line(custo_dia, x='Dia', y='Custo BRL', markers=True,
                          color_discrete_sequence=[COR_PRIMARIA])
        fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10), yaxis_title="Custo (R$)", xaxis_type='category')
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados.")

    st.divider()
    st.markdown("### 🔄 Motivos de transferência / encerramento")
    if not df_conv_p.empty and 'telefone' in df_conv_p.columns:
        motivos = {
            "🤝 Coordenadora": 0, "👤 Recepção (humano)": 0, "📅 Agendou": 0,
            "✋ Encerrou": 0, "Sem desfecho": 0,
        }

        # Sets de lookup rápido — telefones que tiveram cada tipo de desfecho no período
        tel_agendou = set()
        if df_agend is not None and not df_agend.empty and 'telefone' in df_agend.columns:
            df_ag_per = df_agend[df_agend['criado_em'] >= dt_inicio] if 'criado_em' in df_agend.columns else df_agend
            tel_agendou = set(df_ag_per['telefone'].astype(str))

        tel_transf_coord = set()
        tel_transf_humano = set()
        if not df_leads.empty and 'transferido_em' in df_leads.columns:
            df_t = df_leads[df_leads['transferido_em'].notna()].copy()
            if not df_t.empty:
                df_t['transferido_em'] = pd.to_datetime(df_t['transferido_em'], errors='coerce')
                df_t = df_t[df_t['transferido_em'] >= dt_inicio]
                if 'transferido_para' in df_t.columns:
                    mask_humano = df_t['transferido_para'].fillna('').str.startswith('Recepção')
                    tel_transf_humano = set(df_t[mask_humano]['telefone'].astype(str))
                    tel_transf_coord = set(df_t[~mask_humano]['telefone'].astype(str))
                else:
                    tel_transf_coord = set(df_t['telefone'].astype(str))

        # Encerramentos ainda via texto (tag [ENCERRAR] não tem tabela; rara mas captura)
        df_bia = df_conv_p[df_conv_p['papel'] == 'assistant'] if 'papel' in df_conv_p.columns else pd.DataFrame()
        tel_encerrou = set()
        if not df_bia.empty:
            for tel in df_conv_p['telefone'].unique():
                msgs_tel = df_bia[df_bia['telefone'] == tel]['mensagem'].str.lower().fillna('')
                todas = ' '.join(msgs_tel.tolist())
                if '[encerrar]' in todas or 'transferir_humano|encerrar' in todas:
                    tel_encerrou.add(str(tel))

        # Classificação (prioridade: agendou > coordenadora > humano > encerrou > sem desfecho)
        for tel in df_conv_p['telefone'].astype(str).unique():
            if tel in tel_agendou:
                motivos["📅 Agendou"] += 1
            elif tel in tel_transf_coord:
                motivos["🤝 Coordenadora"] += 1
            elif tel in tel_transf_humano:
                motivos["👤 Recepção (humano)"] += 1
            elif tel in tel_encerrou:
                motivos["✋ Encerrou"] += 1
            else:
                motivos["Sem desfecho"] += 1

        df_motivos = pd.DataFrame(list(motivos.items()), columns=['Motivo', 'Conversas'])
        fig = px.bar(df_motivos, x='Conversas', y='Motivo', orientation='h',
                     color_discrete_sequence=[COR_PRIMARIA], text='Conversas')
        fig.update_traces(textposition='outside')
        fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sem dados.")

    st.divider()
    st.markdown("### ⚠️ Conversas que precisam de atenção")
    # v5.1: passa df_bia_disparos pra resolver nome/unidade dos indicados
    df_agrupado_p = agrupar_conversas(df_conv_p, df_leads, df_agend, df_bia_disparos=df_bia_disparos)
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


# ─────────── ABA 5: ⚙️ CONFIGURAÇÕES (mantida + modelo Sonnet) ───────────

def tela_configuracoes():
    st.markdown("## ⚙️ Configurações")
    cfg = carregar_configuracoes()

    st.success("✅ Configurações integradas ao Supabase — ao salvar, o n8n vai buscar os números automaticamente.")

    st.info(
        "ℹ️ A Bia v5 não notifica mais a recepção por WhatsApp — a fila de casos "
        "abertos fica na aba **⚠️ Pendências**. Os números abaixo são usados só "
        "como fallback caso algum dia o nó *Notifica Recepção* seja reativado."
    )

    st.markdown("### 🙋 WhatsApp das recepções")
    st.caption("Quando a Bia disparar [TRANSFERIR_HUMANO] (reagendamento, cliente confuso, restrição médica), o aviso vai pra esses números.")

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.markdown("**Mogi das Cruzes**")
        recep_mogi = st.text_input("Número (com DDD, só dígitos)", value=cfg.get('recepcao_mogi_telefone', ''),
            key='input_recep_mogi', placeholder="11999999999")
        nome_recep_mogi = st.text_input("Nome da recepção", value=cfg.get('recepcao_mogi_nome', ''),
            key='input_nome_recep_mogi', placeholder="Ex: Recepção Mogi")
    with col_r2:
        st.markdown("**Suzano**")
        recep_suzano = st.text_input("Número (com DDD, só dígitos)", value=cfg.get('recepcao_suzano_telefone', ''),
            key='input_recep_suzano', placeholder="11999999999")
        nome_recep_suzano = st.text_input("Nome da recepção", value=cfg.get('recepcao_suzano_nome', ''),
            key='input_nome_recep_suzano', placeholder="Ex: Recepção Suzano")

    st.divider()

    st.markdown("### 🛠️ Modo manutenção")
    manutencao = st.toggle("Pausar a Bia (ela para de responder)", value=cfg.get('modo_manutencao', False),
        help="Quando ligado, o n8n vai checar essa flag e não processar novas mensagens.")
    if manutencao:
        st.warning("⚠️ Modo manutenção ATIVO — a Bia vai parar de responder quando o n8n checar essa flag.")

    st.divider()

    if st.button("💾 Salvar no Supabase", type="primary"):
        ok = salvar_configuracoes(
            modo_manutencao=manutencao,
            recepcao_mogi_telefone=recep_mogi, recepcao_mogi_nome=nome_recep_mogi,
            recepcao_suzano_telefone=recep_suzano, recepcao_suzano_nome=nome_recep_suzano,
        )
        if ok:
            st.success("✅ Salvo! O n8n vai usar esses dados na próxima transferência.")
            st.balloons()

    st.divider()

    st.markdown("### 📊 Informações do sistema")
    col_i1, col_i2, col_i3, col_i4 = st.columns(4)
    with col_i1:
        st.markdown(render_metric_card("🧠", VERSAO_CEREBRO, "Cérebro", "primary"), unsafe_allow_html=True)
    with col_i2:
        st.markdown(render_metric_card("🤖", MODELO_CLAUDE_DEFAULT, "Modelo Claude", "purple"), unsafe_allow_html=True)
    with col_i3:
        st.markdown(render_metric_card("📊", VERSAO_DASHBOARD, "Dashboard", "blue"), unsafe_allow_html=True)
    with col_i4:
        st.markdown(render_metric_card("🟢", "Online", "Webhook n8n", "green"), unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.caption("Webhook: https://maislaser-robo.app.n8n.cloud/webhook/maislaser-whatsapp")

    if cfg.get('atualizado_em'):
        st.caption(f"Última atualização das configs: {cfg['atualizado_em'][:19].replace('T', ' ')}")


# ============================================================================
# 10) MAIN
# ============================================================================

def main():
    if not check_password():
        st.stop()

    # Inicializa o robô ativo (default = Bia)
    if 'robo_ativo' not in st.session_state:
        st.session_state['robo_ativo'] = 'bia'

    robo = st.session_state['robo_ativo']

    with st.sidebar:
        # Logo card no topo
        st.markdown(f"""
        <div class="logo-card">
            {_get_logo_html()}
            <div class="logo-subtitle">Dashboard</div>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🔄 Atualizar dados", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        auto_refresh = st.checkbox("Auto-refresh a cada 30s", value=False)

        st.markdown("<br>", unsafe_allow_html=True)

        # ─── KILL SWITCH (só Bia) ───────────────────────────────
        if robo == 'bia':
            try:
                _cfg_ks = carregar_configuracoes()
                _bia_pausada = bool(_cfg_ks.get('modo_manutencao', False)) if _cfg_ks else False
            except Exception:
                _bia_pausada = False

            _ks_class = "kill-switch-box paused" if _bia_pausada else "kill-switch-box"
            _ks_status = "🔴 Bia PAUSADA" if _bia_pausada else "🟢 Bia ATIVA"
            _ks_sub = "Crons e MVP parados" if _bia_pausada else "Recebendo mensagens"

            st.markdown(f"""
            <div class="{_ks_class}">
                <div class="kill-switch-label">Status do robô</div>
                <div class="kill-switch-status">{_ks_status}</div>
                <div class="kill-switch-sub">{_ks_sub}</div>
            </div>
            """, unsafe_allow_html=True)

            _btn_label = "▶️ Reativar Bia" if _bia_pausada else "⏸️ Pausar Bia"
            _btn_type = "primary" if _bia_pausada else "secondary"
            if st.button(_btn_label, key="kill_switch_btn",
                         type=_btn_type, use_container_width=True):
                if set_modo_manutencao(not _bia_pausada):
                    st.toast(
                        "Bia reativada" if _bia_pausada else "Bia pausada",
                        icon="✅" if _bia_pausada else "⏸️"
                    )
                    st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)

        # ─── Seletor de robô ────────────────────────────────────
        st.markdown(
            '<div class="sidebar-info-label" style="font-size: 11px; '
            'margin-bottom: 8px; letter-spacing: 0.06em;">ROBÔ ATIVO</div>',
            unsafe_allow_html=True
        )
        for key, label in ROBOS.items():
            is_active = (robo == key)
            btn_type = "primary" if is_active else "secondary"
            if st.button(label, key=f"sel_robo_{key}", type=btn_type, use_container_width=True):
                if robo != key:
                    st.session_state['robo_ativo'] = key
                    # Limpa estado que é só da Bia
                    if 'conversa_selecionada' in st.session_state:
                        del st.session_state['conversa_selecionada']
                    st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("🚪 Sair", use_container_width=True):
            st.session_state["password_correct"] = False
            if "t" in st.query_params:
                del st.query_params["t"]
            st.rerun()

    # ════════════════════════════════════════════════════════════
    # CONTEÚDO PRINCIPAL — bifurca conforme robô ativo
    # ════════════════════════════════════════════════════════════

    if robo == 'bia':
        with st.spinner("Carregando dados..."):
            df_conv = carregar_conversas(dias_atras=30)
            df_leads = carregar_leads()
            df_agend = carregar_agendamentos()
            df_clientes_base = carregar_clientes_base_nomes()
            df_bia_disparos = carregar_bia_disparos()  # v5.1: fallback nome/unidade

        # v5.2: suporta abrir conversa direto via URL (?conversa=5511XXX)
        # usado pelo botão "🔗" da lista de conversas pra abrir em nova guia
        _qp_main = st.query_params
        if "conversa" in _qp_main and "conversa_selecionada" not in st.session_state:
            st.session_state['conversa_selecionada'] = _qp_main.get("conversa")

        if 'conversa_selecionada' in st.session_state and st.session_state['conversa_selecionada']:
            if st.button("← Voltar pra lista"):
                del st.session_state['conversa_selecionada']
                # Limpa query param "conversa" pra não reabrir no próximo refresh
                if "conversa" in st.query_params:
                    del st.query_params["conversa"]
                st.rerun()
            renderizar_conversa(st.session_state['conversa_selecionada'],
                                df_conv, df_leads, df_agend, df_clientes_base, df_bia_disparos)
        else:
            tab_pend, tab1, tab3, tab_base, tab4, tab5 = st.tabs([
                "⚠️ Pendências",
                "💬 Conversas",
                "📅 Agendamentos",
                "📊 Base de Clientes",
                "📈 Métricas",
                "⚙️ Configurações",
            ])

            with tab_pend:
                render_aba_pendencias()

            with tab1:
                tela_conversas(df_conv, df_leads, df_agend, df_clientes_base, df_bia_disparos)

            with tab3:
                tela_agendamentos(df_agend, df_leads, df_conv)

            with tab_base:
                render_aba_base_clientes(get_supabase())

            with tab4:
                tela_metricas(df_conv, df_leads, df_agend, df_bia_disparos)

            with tab5:
                tela_configuracoes()
    elif robo == 'confirmacao':
        # ─── Tabs do Robô Confirmação Agenda ────────────────────
        # Fase C: 4 abas conectadas aos endpoints read-only do Apps Script.
        # Disparar agenda continua placeholder até a Fase D (porta o app.py).
        tab_disp, tab_hist_disp, tab_dia, tab_hist, tab_indic, tab_metr = st.tabs([
            "🚀 Disparar agenda",
            "📋 Histórico de disparos",
            "📅 Disparos do dia",
            "💬 Histórico de respostas",
            "🎁 Programa de indicações",
            "📊 Métricas confirmação",
        ])

        with tab_disp:
            render_aba_disparador()

        with tab_hist_disp:
            render_aba_historico_disparos()

        with tab_dia:
            tela_confirmacao_disparos_dia()

        with tab_hist:
            tela_confirmacao_historico()

        with tab_indic:
            tela_confirmacao_indicacoes()

        with tab_metr:
            tela_confirmacao_metricas()

    elif robo == 'zapi':
        # ─── Tabs do Robô Z-API Indicações ──────────────────────
        # v9.8 (18/06/2026): adicionada tab Histórico Bia entre Indicações e Ranking
        tab_aguard, tab_clientes, tab_indic, tab_hist_bia, tab_rank, tab_metr = st.tabs([
            "⏳ Aguardando validação",
            "👥 Clientes no programa",
            "📨 Indicações",
            "📜 Histórico Bia",
            "🏆 Ranking funcionárias",
            "📊 Métricas",
        ])

        with tab_aguard:
            render_aba_zapi_aguardando()

        with tab_clientes:
            render_aba_zapi_clientes()

        with tab_indic:
            render_aba_zapi_indicacoes()

        with tab_hist_bia:
            render_aba_historico_bia()

        with tab_rank:
            render_aba_zapi_ranking()

        with tab_metr:
            render_aba_zapi_metricas()

    if auto_refresh:
        time.sleep(30)
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
