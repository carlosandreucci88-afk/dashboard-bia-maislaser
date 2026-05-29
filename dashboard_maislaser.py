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

def check_password():
    """Tela de login simples por senha."""
    
    def password_entered():
        if st.session_state["password"] == st.secrets.get("DASHBOARD_PASSWORD", "maislaser"):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False
    
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    
    if not st.session_state["password_correct"]:
        st.markdown("# 💚 Dashboard Bia — MaisLaser")
        st.markdown("### Entre com a senha pra acessar")
        st.text_input(
            "Senha",
            type="password",
            on_change=password_entered,
            key="password",
            placeholder="Digite a senha e tecle Enter"
        )
        if "password" in st.session_state and not st.session_state["password_correct"]:
            st.error("❌ Senha incorreta")
        return False
    return True


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

def agrupar_conversas(df_conv, df_leads):
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
    
    # Junta com leads pra pegar nome/unidade/status
    if not df_leads.empty:
        grouped = grouped.merge(
            df_leads[['telefone', 'nome', 'unidade', 'status', 'tipo_cliente', 'genero']],
            on='telefone',
            how='left'
        )
    else:
        grouped['nome'] = None
        grouped['unidade'] = None
        grouped['status'] = None
        grouped['tipo_cliente'] = None
        grouped['genero'] = None
    
    # Marca alertas
    grouped['alertas'] = grouped.apply(lambda row: detectar_alertas(row, df_conv), axis=1)
    
    return grouped.sort_values('ultima_atualizacao', ascending=False)


def detectar_alertas(row, df_conv):
    """Detecta possíveis problemas em uma conversa."""
    alertas = []
    
    telefone = row['telefone']
    msgs_dessa_conv = df_conv[df_conv['telefone'] == telefone].sort_values('criado_em')
    
    if msgs_dessa_conv.empty:
        return alertas
    
    # Concatena todas as mensagens da Bia
    msgs_bia = msgs_dessa_conv[msgs_dessa_conv['papel'] == 'assistant']['mensagem'].str.lower().fillna('')
    msgs_user = msgs_dessa_conv[msgs_dessa_conv['papel'] == 'user']['mensagem'].str.lower().fillna('')
    
    todas_bia = ' '.join(msgs_bia.tolist())
    todas_user = ' '.join(msgs_user.tolist())
    
    # ALERTA 1: cliente disse "já sou cliente" mas Bia depois falou em "presente"
    sinais_cliente_existente = ['já sou cliente', 'ja sou cliente', 'já faço aí', 'perdi a sessão', 
                                  'perdi minha sessão', 'quero reagendar', 'quero remarcar', 'já fiz aí']
    cliente_se_identificou = any(s in todas_user for s in sinais_cliente_existente)
    
    if cliente_se_identificou:
        # Verifica se DEPOIS da identificação a Bia falou de presente
        idx_identificacao = None
        for idx, msg in msgs_dessa_conv.iterrows():
            if msg['papel'] == 'user' and any(s in str(msg['mensagem']).lower() for s in sinais_cliente_existente):
                idx_identificacao = idx
                break
        
        if idx_identificacao is not None:
            msgs_bia_depois = msgs_dessa_conv[
                (msgs_dessa_conv.index > idx_identificacao) & 
                (msgs_dessa_conv['papel'] == 'assistant')
            ]['mensagem'].str.lower().fillna('').tolist()
            
            if any('presente' in m or 'ganhou' in m or '5 sessões' in m or 'cortesia' in m for m in msgs_bia_depois):
                alertas.append('🔴 Falou de presente pra cliente existente')
    
    # ALERTA 2: conversa longa sem desfecho (>8 msgs sem tag)
    if row['total_mensagens'] > 8:
        if not any(tag in todas_bia for tag in ['transferir_coordenadora', 'transferir_humano', 'agendar', 'encerrar']):
            alertas.append('🟡 Conversa longa sem desfecho')
    
    # ALERTA 3: cliente perguntou preço e Bia não transferiu
    if any(p in todas_user for p in ['quanto custa', 'qual o preço', 'qual o valor', 'parcelamento', 'desconto']):
        msgs_bia_recentes = msgs_bia.tolist()[-3:] if len(msgs_bia) >= 3 else msgs_bia.tolist()
        recentes_txt = ' '.join(msgs_bia_recentes)
        if 'transferir_coordenadora' not in recentes_txt and 'coordenadora' not in recentes_txt:
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

def tela_conversas(df_conv, df_leads):
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
    
    df_conv = df_conv[df_conv['criado_em'] >= dt_inicio]
    
    # Agrupa por telefone
    df_agrupado = agrupar_conversas(df_conv, df_leads)
    
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
        c3.text((row['unidade'] or '-').title())
        
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
    
    df_conv_p = df_conv[df_conv['criado_em'] >= dt_inicio]
    
    # ─── Cards de topo ──────────────────────────────────────────────
    st.markdown("### Resumo do período")
    
    conversas_unicas = df_conv_p['telefone'].nunique() if not df_conv_p.empty else 0
    total_msgs = len(df_conv_p)
    msgs_bia = len(df_conv_p[df_conv_p['papel'] == 'assistant'])
    tokens_total = int(df_conv_p['tokens'].sum()) if not df_conv_p.empty else 0
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
        msgs_por_tel = df_conv_p.groupby('telefone').size()
        engajaram = (msgs_por_tel >= 3).sum()
        
        # Transferiram = conversas com tag de transferência
        df_conv_bia = df_conv_p[df_conv_p['papel'] == 'assistant']
        transferiram = 0
        agendaram = 0
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
        if not df_conv_p.empty:
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
        if not df_leads.empty:
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
    if not df_conv_p.empty:
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
    df_agrupado_p = agrupar_conversas(df_conv_p, df_leads)
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
    """Tela de configurações da Bia."""
    st.markdown("## ⚙️ Configurações")
    
    st.info("Estas configurações ainda não estão conectadas ao fluxo do n8n. Por enquanto, é só pra você guardar os números e visualizar — vamos integrar quando implementar a ramificação [TRANSFERIR_COORDENADORA].")
    
    st.markdown("### 📞 WhatsApp das coordenadoras de vendas")
    st.caption("Quando a Bia disparar [TRANSFERIR_COORDENADORA], ela vai mandar um aviso pra esses números.")
    
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown("**Mogi das Cruzes**")
        coord_mogi = st.text_input(
            "Número (com DDD, só dígitos)",
            value=st.session_state.get('coord_mogi', ''),
            key='input_coord_mogi',
            placeholder="11999999999"
        )
        nome_coord_mogi = st.text_input(
            "Nome da coordenadora",
            value=st.session_state.get('nome_coord_mogi', ''),
            key='input_nome_coord_mogi',
            placeholder="Ex: Juliana"
        )
    with col_c2:
        st.markdown("**Suzano**")
        coord_suzano = st.text_input(
            "Número (com DDD, só dígitos)",
            value=st.session_state.get('coord_suzano', ''),
            key='input_coord_suzano',
            placeholder="11999999999"
        )
        nome_coord_suzano = st.text_input(
            "Nome da coordenadora",
            value=st.session_state.get('nome_coord_suzano', ''),
            key='input_nome_coord_suzano',
            placeholder="Ex: Renata"
        )
    
    if st.button("💾 Salvar (sessão local)", type="primary"):
        st.session_state['coord_mogi'] = coord_mogi
        st.session_state['nome_coord_mogi'] = nome_coord_mogi
        st.session_state['coord_suzano'] = coord_suzano
        st.session_state['nome_coord_suzano'] = nome_coord_suzano
        st.success("✅ Salvo na sessão! (precisa integrar com o n8n pra valer de verdade)")
    
    st.divider()
    
    st.markdown("### 🛠️ Modo manutenção")
    manutencao = st.toggle(
        "Pausar a Bia (ela para de responder)",
        value=st.session_state.get('manutencao', False),
        help="Quando ligado, a Bia para de responder novas mensagens. Útil pra testes ou pra parar tudo em emergência."
    )
    st.session_state['manutencao'] = manutencao
    if manutencao:
        st.warning("⚠️ Modo manutenção ATIVO. Lembrando que essa configuração ainda não está ligada ao n8n — quando integrar, ela vai funcionar de verdade.")
    
    st.divider()
    
    st.markdown("### 📊 Informações do sistema")
    col_i1, col_i2 = st.columns(2)
    with col_i1:
        st.metric("Versão do cérebro", "v3.2")
        st.metric("Modelo Claude", "claude-haiku-4-5")
    with col_i2:
        st.metric("Webhook n8n", "✅ Online")
        st.caption("https://maislaser-robo.app.n8n.cloud/webhook/maislaser-whatsapp")


# ============================================================================
# MAIN
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
        st.caption("**Versão Cérebro:** v3.2")
        st.caption("**Modelo:** claude-haiku-4-5")
        
        st.divider()
        if st.button("🚪 Sair", use_container_width=True):
            st.session_state["password_correct"] = False
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
        # Abas principais
        tab1, tab2, tab3 = st.tabs(["💬 Conversas", "📈 Métricas", "⚙️ Configurações"])
        
        with tab1:
            tela_conversas(df_conv, df_leads)
        
        with tab2:
            tela_metricas(df_conv, df_leads, df_agend)
        
        with tab3:
            tela_configuracoes()
    
    # Auto refresh
    if auto_refresh:
        time.sleep(30)
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
