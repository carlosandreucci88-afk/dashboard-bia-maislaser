"""
==============================================================================
DASHBOARD MAISLASER
==============================================================================
v6.6 (04/07/2026): NOVO 🚀 Robô Pós-atendimento (número 97502-5297)
  - Adiciona 3 abas: Disparar / Histórico / Monitoramento clientes
  - Arquitetura moderna: SEM Sheets, direto Supabase
  - Envio de template via Meta API pela dashboard
  - Apps Script separado apenas pra webhook doPost
  - Tabelas: pos_atendimento_clientes, _log, _disparos_historico
  - RPC: pos_get_stats(dias)

v6.5 (04/07/2026): NOVA ABA "🔧 Diagnóstico" no Robô Z-API (Fase 4.9)
  - Adiciona aba de diagnóstico completo do robô Bia como última tab
  - Usa render_aba_zapi_diagnostico do aba_zapi.py

v6.4 (03/07/2026): NOVA ABA "👥 Funcionárias" no Robô Z-API (Fase 4.6)
  - Adiciona aba CRUD de funcionárias logo após "🏆 Ranking funcionárias"
  - Usa render_aba_zapi_funcionarias do aba_zapi.py
  - Lê/escreve direto no Supabase (tabela funcionarias, campo ativa)

v6.3 (01/07/2026): REINTRODUZ ABA BASE DE CLIENTES
  - Adiciona `📊 Base de clientes` como última tab do Robô Z-API
  - Reintroduz import de `render_aba_base_clientes` (arquivo já existia no
    repo desde a demolição v6.1, só o import foi removido)
  - Passa `get_supabase()` pra função (contrato que ela espera)

v6.1 (30/06/2026): DEMOLIÇÃO FINAL do robô Bia conversacional.
  - Remove "🤖 Robô Bia IA" do dashboard inteiro (tabelas conversas e
    agendamentos foram dropadas na migration_v3_demolicao.sql; o modelo
    FSM com Cérebro Determinístico foi substituído pelo modelo AUTO).
  - Default agora é Robô Confirmação Agenda.

v6.0 (30/06/2026): aba "🤖 Disparador AUTO" no robô Z-API (novo modelo).
==============================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
import time
import hashlib
import base64
import os

# Módulos das abas
from aba_base_clientes import render_aba_base_clientes
from aba_confirmacao import (
    tela_confirmacao_disparos_dia,
    tela_confirmacao_historico,
    tela_confirmacao_indicacoes,
    tela_confirmacao_metricas,
)
from aba_disparador import render_aba_disparador
from aba_zapi import (
    render_aba_zapi_aguardando,
    render_aba_zapi_ranking,
    render_aba_zapi_indicacoes,
    render_aba_zapi_metricas,
    render_aba_zapi_clientes,
    render_aba_zapi_funcionarias,  # v6.4: CRUD funcionárias (Fase 4.6)
    render_aba_zapi_diagnostico,   # v6.5: Diagnóstico (Fase 4.9)
)
from aba_historico_disparos import render_aba_historico_disparos
from aba_disparador_auto import render_aba_disparador_auto
from aba_conversas import render_aba_conversas
# v6.6: Robô Pós-atendimento (número 97502-5297)
from aba_pos_disparar import render_aba_pos_disparar
from aba_pos_historico_monitor import render_aba_pos_historico, render_aba_pos_monitor
from aba_pos_ranking import render_aba_pos_ranking
from aba_pos_config import render_aba_pos_config
from aba_pos_diagnostico import render_aba_pos_diagnostico  # v6.7: aba diagnóstico
# v6.9: aba de saúde consolidada (sidebar + página completa)
from aba_saude import render as render_saude, render_sidebar as render_sidebar_saude


# ============================================================================
# CONFIG INICIAL
# ============================================================================

st.set_page_config(
    page_title="Maislaser · Dashboard",
    page_icon="💚",
    layout="wide",
    initial_sidebar_state="expanded",
)

TZ_SP = timezone(timedelta(hours=-3))
COR_PRIMARIA = "#5BC0BE"
COR_PRIMARIA_DARK = "#3D9991"
VERSAO_DASHBOARD = "v6.6"

# v6.1: só 2 robôs (Bia conversacional morreu na demolição)
# v6.6: adiciona robô Pós-atendimento (número 97502-5297)
ROBOS = {
    'confirmacao': '📅 Robô Confirmação Agenda',
    'zapi':        '🎁 Robô Z-API Indicações',
    'pos':         '🚀 Robô Pós-atendimento',
}


# ============================================================================
# CSS — visual moderno (paleta teal do logo)
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

.stApp { background-color: var(--bg-page); font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
.stApp p, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp label, .stApp button, .stApp input, .stApp textarea, .stApp select { font-family: 'Inter', sans-serif; }
[data-testid="stIconMaterial"], .material-icons, .material-symbols-outlined,
[class*="material-symbols"], [class*="MaterialSymbols"] { font-family: 'Material Symbols Outlined', 'Material Symbols Rounded', 'Material Icons' !important; }

h1, h2, h3, h4 { color: var(--text); font-weight: 700 !important; letter-spacing: -0.02em; }
h1 { font-size: 28px !important; }
h2 { font-size: 22px !important; }
h3 { font-size: 18px !important; }

/* SIDEBAR */
[data-testid="stSidebar"] { background: linear-gradient(180deg, #5BC0BE 0%, #3D9991 100%); border-right: none; }
[data-testid="stSidebar"] > div:first-child { padding-top: 1.5rem; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
[data-testid="stSidebar"] p, [data-testid="stSidebar"] label, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: white !important; }
[data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea { color: var(--text) !important; background: rgba(255,255,255,0.95) !important; }
[data-testid="stSidebar"] [data-testid="stIconMaterial"],
[data-testid="stSidebar"] .material-icons,
[data-testid="stSidebar"] .material-symbols-outlined { color: white !important; }
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapseButton"] * { color: white !important; fill: white !important; }

.logo-card { background: white; border-radius: 14px; padding: 20px 16px 16px 16px; margin: 0 -4px 20px -4px; box-shadow: 0 6px 20px rgba(0,0,0,0.18); text-align: center; }
.logo-card img { width: 100%; max-width: 180px; height: auto; display: block; margin: 0 auto; }
.logo-card .logo-subtitle { font-size: 11px; color: var(--text-secondary) !important; margin-top: 8px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; }

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

.sidebar-info { background: rgba(255,255,255,0.15); border-radius: 10px; padding: 10px 14px; margin: 6px 0; backdrop-filter: blur(10px); }
.sidebar-info-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px; opacity: 0.85; margin-bottom: 2px; }
.sidebar-info-value { font-size: 13px; font-weight: 600; }

/* TABS */
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
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { background-color: var(--primary) !important; }

/* MÉTRICAS NATIVAS */
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

/* CARDS DE MÉTRICA CUSTOMIZADOS */
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

/* INPUTS */
.stTextInput input, .stTextArea textarea, .stSelectbox > div > div {
    border-radius: var(--radius-sm) !important;
    border-color: var(--border) !important;
    background: white !important;
}
.stTextInput input:focus { border-color: var(--primary) !important; box-shadow: 0 0 0 3px var(--primary-bg) !important; }

/* BOTÕES */
.main .stButton > button { border-radius: var(--radius-sm) !important; font-weight: 600 !important; transition: all 0.2s ease; }

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

/* BADGES */
.badge-alerta { background: #fee2e2; color: #991b1b; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-ok { background: #dcfce7; color: #166534; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-info { background: var(--primary-bg); color: var(--primary-dark); padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-amber { background: #fef3c7; color: #92400e; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-purple { background: #ede9fe; color: #5b21b6; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge-neutral { background: #f3f4f6; color: #4b5563; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }

hr { border-color: var(--border-light) !important; margin: 1.5rem 0 !important; }
.stCaption, [data-testid="stCaptionContainer"] { color: var(--text-secondary) !important; font-size: 13px !important; }

[data-testid="stDataFrame"] { border-radius: var(--radius) !important; border: 1px solid var(--border) !important; overflow: hidden; }
[data-testid="stAlert"] { border-radius: var(--radius) !important; border: 1px solid var(--border) !important; box-shadow: var(--shadow-sm); }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# HELPERS DE UI — logo e cards
# ============================================================================

@st.cache_data
def _get_logo_html():
    """HTML do logo em base64. Fallback gracioso se arquivo não existir."""
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
# AUTENTICAÇÃO SIMPLES
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
            div[data-testid="stTextInput"] label { display: none; }
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
            div[data-testid="stForm"] button[kind="primary"] {
                height: 44px;
                font-weight: 600;
            }
        </style>
    """, unsafe_allow_html=True)

    col_esq, col_meio, col_dir = st.columns([1, 1, 1])
    with col_meio:
        st.markdown(f"""
            <div class="bia-login-header">
                {_get_logo_html()}
            </div>
        """, unsafe_allow_html=True)

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
# CONEXÃO SUPABASE — usada por todas as abas via lazy import
# ============================================================================

@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# ============================================================================
# MAIN
# ============================================================================

def main():
    if not check_password():
        st.stop()

    # v6.2: se URL tem ?conv_id=xxx, mostra timeline da conversa em fullscreen
    # (sem sidebar de robôs, sem abas). Usado quando abre conversa em nova aba.
    qp = st.query_params
    if "conv_id" in qp:
        from aba_conversas import render_conversa_fullscreen
        render_conversa_fullscreen(qp["conv_id"])
        return

    # v6.1: robô Bia conversacional foi removido. Default = confirmacao.
    # Sessões antigas com robo_ativo='bia' são normalizadas pra confirmacao.
    # v6.9: 'saude' é modo válido mesmo não estando em ROBOS (não vira botão).
    _MODOS_VALIDOS = set(ROBOS.keys()) | {'saude'}
    if ('robo_ativo' not in st.session_state
            or st.session_state['robo_ativo'] not in _MODOS_VALIDOS):
        st.session_state['robo_ativo'] = 'confirmacao'

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

        # Seletor de robô
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
                    st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        # v6.9: mini-widget de saúde do sistema (3 semáforos + botão detalhes)
        render_sidebar_saude()

        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("🚪 Sair", use_container_width=True):
            st.session_state["password_correct"] = False
            if "t" in st.query_params:
                del st.query_params["t"]
            st.rerun()

    # ════════════════════════════════════════════════════════════
    # CONTEÚDO PRINCIPAL — bifurca conforme robô ativo
    # ════════════════════════════════════════════════════════════

    if robo == 'confirmacao':
        (tab_disp, tab_hist_disp, tab_dia, tab_hist,
         tab_indic, tab_metr) = st.tabs([
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
        # v6.0: aba "📜 Histórico Bia" substituída por "🤖 Disparador AUTO"
        # v3.6: aba "💬 Conversas" ao lado do Disparador AUTO
        # v6.3: aba "📊 Base de clientes" reintroduzida no final
        # v6.4: aba "👥 Funcionárias" (CRUD) logo após Ranking
        # v6.5: aba "🔧 Diagnóstico" no final (Fase 4.9)
        (tab_aguard, tab_clientes, tab_indic, tab_disp_auto, tab_conv,
         tab_rank, tab_func, tab_metr, tab_base, tab_diag) = st.tabs([
            "⏳ Aguardando validação",
            "👥 Clientes no programa",
            "📨 Indicações",
            "🤖 Disparador AUTO",
            "💬 Conversas",
            "🏆 Ranking funcionárias",
            "👥 Funcionárias",
            "📊 Métricas",
            "📊 Base de clientes",
            "🔧 Diagnóstico",
        ])

        with tab_aguard:
            render_aba_zapi_aguardando()

        with tab_clientes:
            render_aba_zapi_clientes()

        with tab_indic:
            render_aba_zapi_indicacoes()

        with tab_disp_auto:
            render_aba_disparador_auto()

        with tab_conv:
            render_aba_conversas()

        with tab_rank:
            render_aba_zapi_ranking()

        with tab_func:
            render_aba_zapi_funcionarias()

        with tab_metr:
            render_aba_zapi_metricas()

        with tab_base:
            render_aba_base_clientes(get_supabase())

        with tab_diag:
            render_aba_zapi_diagnostico()

    elif robo == 'pos':
        # v6.6: Robô Pós-atendimento (número 97502-5297) — arquitetura sem
        # Sheets, direto Supabase. Apps Script separado só pra webhook doPost.
        # v6.7: aba 🔧 Diagnóstico com RPC pos_diagnostico_completo + ações
        # v6.8 (13/07/2026): aba 🏆 Ranking profissionais
        (tab_pos_disp, tab_pos_hist, tab_pos_mon, tab_pos_rank, tab_pos_cfg,
         tab_pos_diag) = st.tabs([
            "🚀 Disparar pós-atendimento",
            "📋 Histórico de disparos",
            "👥 Monitoramento clientes",
            "🏆 Ranking profissionais",
            "⚙️ Configurações",
            "🔧 Diagnóstico",
        ])

        with tab_pos_disp:
            render_aba_pos_disparar()

        with tab_pos_hist:
            render_aba_pos_historico()

        with tab_pos_mon:
            render_aba_pos_monitor()

        with tab_pos_rank:
            render_aba_pos_ranking()

        with tab_pos_cfg:
            render_aba_pos_config()

        with tab_pos_diag:
            render_aba_pos_diagnostico()

    elif robo == 'saude':
        # v6.9: página completa de saúde consolidada (RPC saude_consolidada)
        render_saude()

    if auto_refresh:
        time.sleep(30)
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
