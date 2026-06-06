"""
==============================================================================
ABA CONFIRMAÇÃO AGENDA — Robô Confirmação (Google Apps Script v6.6+)
==============================================================================
Conecta o dashboard aos endpoints read-only do Apps Script:
  - /?endpoint=contexto  → todas as linhas da planilha Contexto
  - /?endpoint=log       → últimas N linhas do Log de Interações

NÃO faz nenhuma escrita — só leitura. O Apps Script segue dono da verdade.

v2 (Fase C.1):
  • Filtros consistentes em todas as 4 telas:
    - Período: Hoje / Últimas 24h / Últimos 3 dias / Tudo
    - Unidade: Todas / Mogi / Suzano
  • Histórico ganhou coluna Unidade
  • Métricas calculadas localmente a partir do contexto (responsivas aos filtros)
==============================================================================
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from datetime import datetime, timedelta, timezone

TZ_SP = timezone(timedelta(hours=-3))

# Mapeamento de status → emoji + classe de badge
STATUS_EMOJI = {
    "aguardando":              "🟡 Aguardando",
    "confirmado":              "🟢 Confirmado",
    "reagendado":              "🔄 Reagendado",
    "cancelado_sem_resposta":  "🔴 Cancelado",
    "aguardando_recepção":     "🟣 Recepção",
    "redirecionado_recepção":  "🟣 Recepção",
    "indicacao_pendente":      "🟠 Indic. pendente",
    "indicacao_aceita":        "🎁 Indic. aceita",
    "indicacao_recusada":      "⚫ Indic. recusada",
    "indicacao_sem_resposta":  "⚪ Indic. sem resp",
    "sem_contexto":            "❓ Sem contexto",
}


# ============================================================================
# CLIENTE HTTP — UM lugar só, cacheado, com timeout e fallback gracioso
# ============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def _apps_script_get(endpoint: str, **params):
    """
    Chama um endpoint read-only do Apps Script.
    Cache 30s. Timeout 15s. Se falhar, retorna {'_erro': '...'}.
    """
    try:
        url = st.secrets["APPS_SCRIPT_URL"]
        token = st.secrets["APPS_SCRIPT_TOKEN"]
    except Exception:
        return {"_erro": "Configuração ausente: adicione APPS_SCRIPT_URL e APPS_SCRIPT_TOKEN nos secrets do Streamlit."}

    query = {"endpoint": endpoint, "token": token, **{k: v for k, v in params.items() if v is not None}}
    try:
        resp = requests.get(url, params=query, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code} ao chamar {endpoint}"}
        data = resp.json()
        if isinstance(data, dict) and data.get("erro"):
            return {"_erro": f"Apps Script: {data['erro']}"}
        return data
    except requests.exceptions.Timeout:
        return {"_erro": "Apps Script demorou demais (>15s). Tente atualizar."}
    except requests.exceptions.RequestException as e:
        return {"_erro": f"Erro de rede: {e}"}
    except ValueError:
        return {"_erro": "Resposta do Apps Script não é JSON válido."}


def _mostrar_erro_e_parar(data, contexto=""):
    """Helper: se data tem _erro, mostra alert e retorna True (caller deve return)."""
    if isinstance(data, dict) and data.get("_erro"):
        st.error(f"⚠️ **Robô Confirmação temporariamente indisponível** {contexto}\n\n{data['_erro']}")
        st.caption("Os dados são lidos diretamente da planilha do Google Sheets via Apps Script. O robô em si continua rodando normalmente — apenas a leitura no dashboard falhou.")
        if st.button("🔄 Tentar novamente", key=f"retry_{contexto}"):
            st.cache_data.clear()
            st.rerun()
        return True
    return False


def _render_metric_card_local(icon, value, label, color="primary", sub=None):
    """Versão local do card pra não depender de import circular."""
    color_map = {
        'primary': '#5BC0BE', 'green': '#22c55e', 'red': '#ef4444',
        'amber': '#f59e0b', 'blue': '#3b82f6', 'purple': '#8b5cf6', 'neutral': '#9ca3af',
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


# ============================================================================
# CONVERSÕES DE TIPOS
# ============================================================================

def _ctx_to_df(data):
    """Converte resposta do endpoint contexto em DataFrame com timestamp_sp."""
    if not isinstance(data, dict) or "linhas" not in data:
        return pd.DataFrame()
    df = pd.DataFrame(data["linhas"])
    if df.empty:
        return df
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
        try:
            df["timestamp_sp"] = df["timestamp"].dt.tz_convert(TZ_SP)
        except Exception:
            df["timestamp_sp"] = df["timestamp"]
    if "status" in df.columns:
        df["status"] = df["status"].astype(str).str.strip()
    return df


def _log_to_df(data):
    """Converte resposta do endpoint log em DataFrame."""
    if not isinstance(data, dict) or "linhas" not in data:
        return pd.DataFrame()
    df = pd.DataFrame(data["linhas"])
    if df.empty:
        return df
    if "Data/Hora" in df.columns:
        df["Data/Hora"] = pd.to_datetime(df["Data/Hora"], errors="coerce", utc=True)
        try:
            df["Data/Hora_sp"] = df["Data/Hora"].dt.tz_convert(TZ_SP)
        except Exception:
            df["Data/Hora_sp"] = df["Data/Hora"]
    return df


# ============================================================================
# 🆕 v2 — FILTROS REUSÁVEIS DE PERÍODO + UNIDADE
# ============================================================================
# Renderiza:
#   - 4 botões de período: Hoje / Últimas 24h / Últimos 3 dias / Tudo
#   - 3 botões de unidade: Todas / Mogi / Suzano
# Aplica os filtros no df e retorna o df filtrado.
# Mostra contagens dinâmicas nos botões (refletem o df ANTES de filtrar).
# ============================================================================

def _filtros_periodo_unidade(df, col_data, col_unidade, key_prefix, default_periodo="Hoje"):
    """
    Renderiza UI de filtros e retorna df filtrado.

    Args:
        df: DataFrame com as colunas
        col_data: nome da coluna de timestamp (tz-aware)
        col_unidade: nome da coluna com texto da unidade
        key_prefix: prefixo único para session_state/button keys
        default_periodo: período inicial selecionado ("Hoje", "24h", "3dias", "Tudo")
    """
    state_per  = f"{key_prefix}_periodo"
    state_unid = f"{key_prefix}_unidade"

    if state_per  not in st.session_state: st.session_state[state_per]  = default_periodo
    if state_unid not in st.session_state: st.session_state[state_unid] = "Todas"

    agora = datetime.now(TZ_SP)

    # Contagens para os botões de PERÍODO (baseadas em todo o df, sem filtro de unidade)
    if df.empty or col_data not in df.columns:
        cnt_hoje = cnt_24h = cnt_3d = cnt_tudo = 0
    else:
        ts = df[col_data]
        hoje_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        cnt_hoje = int((ts >= hoje_inicio).sum())
        cnt_24h  = int((ts >= agora - timedelta(hours=24)).sum())
        cnt_3d   = int((ts >= agora - timedelta(days=3)).sum())
        cnt_tudo = len(df)

    # ── Linha 1: botões de PERÍODO ──
    col_p1, col_p2, col_p3, col_p4 = st.columns([1.1, 1.4, 1.5, 1.0])
    with col_p1:
        ativo = st.session_state[state_per] == "Hoje"
        if st.button(f"📆 Hoje ({cnt_hoje})", type="primary" if ativo else "secondary",
                     use_container_width=True, key=f"{key_prefix}_btn_p_hoje"):
            st.session_state[state_per] = "Hoje"; st.rerun()
    with col_p2:
        ativo = st.session_state[state_per] == "24h"
        if st.button(f"🕐 Últimas 24h ({cnt_24h})", type="primary" if ativo else "secondary",
                     use_container_width=True, key=f"{key_prefix}_btn_p_24h"):
            st.session_state[state_per] = "24h"; st.rerun()
    with col_p3:
        ativo = st.session_state[state_per] == "3dias"
        if st.button(f"📅 Últimos 3 dias ({cnt_3d})", type="primary" if ativo else "secondary",
                     use_container_width=True, key=f"{key_prefix}_btn_p_3d"):
            st.session_state[state_per] = "3dias"; st.rerun()
    with col_p4:
        ativo = st.session_state[state_per] == "Tudo"
        if st.button(f"♾️ Tudo ({cnt_tudo})", type="primary" if ativo else "secondary",
                     use_container_width=True, key=f"{key_prefix}_btn_p_tudo"):
            st.session_state[state_per] = "Tudo"; st.rerun()

    # ── Aplica filtro de PERÍODO ──
    df_f = df.copy()
    if col_data in df_f.columns and not df_f.empty:
        per = st.session_state[state_per]
        if per == "Hoje":
            df_f = df_f[df_f[col_data] >= agora.replace(hour=0, minute=0, second=0, microsecond=0)]
        elif per == "24h":
            df_f = df_f[df_f[col_data] >= agora - timedelta(hours=24)]
        elif per == "3dias":
            df_f = df_f[df_f[col_data] >= agora - timedelta(days=3)]
        # "Tudo" → sem filtro

    # Contagens para botões de UNIDADE (baseadas no df já filtrado por período)
    if df_f.empty or col_unidade not in df_f.columns:
        cnt_todas = cnt_mogi = cnt_suzano = 0
    else:
        s_unid = df_f[col_unidade].astype(str).str.lower()
        cnt_todas  = len(df_f)
        cnt_mogi   = int(s_unid.str.contains('mogi', na=False).sum())
        cnt_suzano = int(s_unid.str.contains('suzano', na=False).sum())

    # ── Linha 2: botões de UNIDADE ──
    col_u1, col_u2, col_u3, _esp = st.columns([1.1, 1.4, 1.2, 1.3])
    with col_u1:
        ativo = st.session_state[state_unid] == "Todas"
        if st.button(f"🏢 Todas ({cnt_todas})", type="primary" if ativo else "secondary",
                     use_container_width=True, key=f"{key_prefix}_btn_u_todas"):
            st.session_state[state_unid] = "Todas"; st.rerun()
    with col_u2:
        ativo = st.session_state[state_unid] == "Mogi"
        if st.button(f"📍 Mogi ({cnt_mogi})", type="primary" if ativo else "secondary",
                     use_container_width=True, key=f"{key_prefix}_btn_u_mogi"):
            st.session_state[state_unid] = "Mogi"; st.rerun()
    with col_u3:
        ativo = st.session_state[state_unid] == "Suzano"
        if st.button(f"📍 Suzano ({cnt_suzano})", type="primary" if ativo else "secondary",
                     use_container_width=True, key=f"{key_prefix}_btn_u_suzano"):
            st.session_state[state_unid] = "Suzano"; st.rerun()

    # ── Aplica filtro de UNIDADE ──
    unid = st.session_state[state_unid]
    if unid != "Todas" and col_unidade in df_f.columns and not df_f.empty:
        df_f = df_f[df_f[col_unidade].astype(str).str.lower().str.contains(unid.lower(), na=False)]

    return df_f


# ============================================================================
# ABA 1: 📅 DISPAROS DO DIA
# ============================================================================

def tela_confirmacao_disparos_dia():
    st.markdown("## 📅 Disparos do dia")
    st.caption("Clientes que receberam template de confirmação e o status atual de cada um.")

    data = _apps_script_get("contexto")
    if _mostrar_erro_e_parar(data, "(carregando contexto)"):
        return

    df = _ctx_to_df(data)
    if df.empty:
        st.info("Nenhum disparo registrado ainda.")
        return

    # Filtros de período + unidade
    df_f = _filtros_periodo_unidade(df, "timestamp_sp", "unidade", "dispdia", default_periodo="Hoje")

    st.markdown("")
    busca = st.text_input("🔎 Buscar por nome ou telefone", key="conf_busca_disparos")
    if busca and not df_f.empty:
        bl = busca.lower()
        df_f = df_f[
            df_f['nome'].astype(str).str.lower().str.contains(bl, na=False) |
            df_f['telefone'].astype(str).str.contains(busca, na=False)
        ]

    if df_f.empty:
        st.info("Nenhum cliente nos filtros selecionados.")
        return

    # ─── KPIs ───
    st_lower = df_f['status'].astype(str).str.lower() if 'status' in df_f.columns else pd.Series([], dtype=str)
    qtd_total       = len(df_f)
    qtd_confirmados = int((st_lower == 'confirmado').sum())
    qtd_aguardando  = int((st_lower == 'aguardando').sum())
    qtd_cancelados  = int(st_lower.str.contains('cancelado', na=False).sum())
    qtd_indic       = int(st_lower.str.startswith('indicacao_').sum())

    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    col_m1.markdown(_render_metric_card_local("👥", qtd_total, "Total", "primary"), unsafe_allow_html=True)
    col_m2.markdown(_render_metric_card_local("🟢", qtd_confirmados, "Confirmados", "green"), unsafe_allow_html=True)
    col_m3.markdown(_render_metric_card_local("🟡", qtd_aguardando, "Aguardando", "amber"), unsafe_allow_html=True)
    col_m4.markdown(_render_metric_card_local("🔴", qtd_cancelados, "Cancelados", "red"), unsafe_allow_html=True)
    col_m5.markdown(_render_metric_card_local("🎁", qtd_indic, "Indicações", "purple"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"### Lista de disparos · {len(df_f)} cliente(s)")

    # Ordena por timestamp desc (mais recente primeiro)
    if 'timestamp_sp' in df_f.columns:
        df_f = df_f.sort_values('timestamp_sp', ascending=False)

    # Cabeçalho
    h1, h2, h3, h4, h5, h6 = st.columns([2, 1.3, 0.9, 1.5, 1.3, 1.1])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Horário sessão**")
    h5.markdown("**Quando**")
    h6.markdown("**Status**")
    st.markdown('<hr style="margin: 4px 0 8px 0;">', unsafe_allow_html=True)

    for _, row in df_f.head(100).iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([2, 1.3, 0.9, 1.5, 1.3, 1.1])
        nome = row.get('nome', '—') or '—'
        tel = str(row.get('telefone', '—'))
        unid = str(row.get('unidade', '—') or '—').replace('Mogi das Cruzes', 'Mogi')
        horario = str(row.get('horario', '—') or '—')
        if len(horario) > 24:
            horario = horario[:24] + "…"

        try:
            ts_local = row['timestamp_sp']
            delta = datetime.now(TZ_SP) - ts_local
            if delta.total_seconds() < 60:
                quando = "agora"
            elif delta.total_seconds() < 3600:
                quando = f"{int(delta.total_seconds()/60)}min"
            elif delta.total_seconds() < 86400:
                quando = f"{int(delta.total_seconds()/3600)}h"
            else:
                quando = ts_local.strftime('%d/%m %H:%M')
        except Exception:
            quando = "—"

        status = str(row.get('status', '—') or '—')
        emoji_label = STATUS_EMOJI.get(status, f"❓ {status}")

        c1.markdown(f"<div style='font-weight: 600;'>{nome}</div>", unsafe_allow_html=True)
        c2.markdown(f"<div style='font-size: 12px; color: #6B7280;'>+{tel}</div>", unsafe_allow_html=True)
        c3.write(unid)
        c4.markdown(f"<div style='font-size: 12px; color: #6B7280;'>{horario}</div>", unsafe_allow_html=True)
        c5.markdown(f"<div style='font-size: 12px; color: #9CA3AF;'>{quando}</div>", unsafe_allow_html=True)
        c6.write(emoji_label)

    if len(df_f) > 100:
        st.caption(f"Mostrando 100 de {len(df_f)} disparos. Use filtros pra refinar.")

    st.caption(f"⏱️ Dados em cache por 30s — última atualização: {data.get('gerado_em', '—')[:19].replace('T', ' ')}")


# ============================================================================
# ABA 2: 💬 HISTÓRICO DE RESPOSTAS
# ============================================================================

def tela_confirmacao_historico():
    st.markdown("## 💬 Histórico de respostas")
    st.caption("Log completo de interações entre o robô e os clientes (últimas 500 entradas).")

    data = _apps_script_get("log", limit=500)
    if _mostrar_erro_e_parar(data, "(carregando log)"):
        return

    df = _log_to_df(data)
    if df.empty:
        st.info("Sem registros no log ainda.")
        return

    # Filtros de período + unidade (default 24h pra cobrir noite anterior)
    df_f = _filtros_periodo_unidade(df, "Data/Hora_sp", "Unidade", "hist", default_periodo="24h")

    st.markdown("")

    # Filtros adicionais (tipo + busca)
    col_f1, col_f2 = st.columns([2, 3])
    with col_f1:
        tipos = ["Todos"] + sorted(df_f['Tipo de Mensagem'].dropna().unique().tolist()) if 'Tipo de Mensagem' in df_f.columns else ["Todos"]
        tipo_filtro = st.selectbox("Tipo de mensagem", tipos, key="hist_tipo")
    with col_f2:
        busca = st.text_input("🔎 Buscar (nome, telefone ou observação)", key="hist_busca")

    if tipo_filtro != "Todos" and 'Tipo de Mensagem' in df_f.columns:
        df_f = df_f[df_f['Tipo de Mensagem'] == tipo_filtro]
    if busca:
        bl = busca.lower()
        df_f = df_f[
            df_f['Nome'].astype(str).str.lower().str.contains(bl, na=False) |
            df_f['Telefone'].astype(str).str.contains(busca, na=False) |
            df_f['Observação'].astype(str).str.lower().str.contains(bl, na=False)
        ]

    st.markdown(f"### {len(df_f)} registro(s)")
    if df_f.empty:
        st.info("Nada encontrado com esses filtros.")
        return

    # Cabeçalho COM coluna Unidade
    h1, h2, h3, h4, h5, h6 = st.columns([1.0, 1.5, 0.8, 1.1, 1.0, 2.6])
    h1.markdown("**Quando**")
    h2.markdown("**Cliente**")
    h3.markdown("**Unidade**")
    h4.markdown("**Tipo**")
    h5.markdown("**Status**")
    h6.markdown("**Observação**")
    st.markdown('<hr style="margin: 4px 0 8px 0;">', unsafe_allow_html=True)

    for _, row in df_f.head(200).iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([1.0, 1.5, 0.8, 1.1, 1.0, 2.6])
        try:
            quando = row['Data/Hora_sp'].strftime('%d/%m %H:%M')
        except Exception:
            quando = "—"
        nome = str(row.get('Nome', '—') or '—')
        tel = str(row.get('Telefone', '—'))
        unid_raw = str(row.get('Unidade', '—') or '—')
        if unid_raw in ("-", "None", ""):
            unid = "—"
        else:
            unid = unid_raw.replace('Mogi das Cruzes', 'Mogi')
        tipo = str(row.get('Tipo de Mensagem', '—') or '—')
        st_depois = str(row.get('Status Depois', '—') or '—')
        obs = str(row.get('Observação', '') or '')
        if len(obs) > 70:
            obs = obs[:70] + "…"

        c1.markdown(f"<div style='font-size: 12px; color: #6B7280;'>{quando}</div>", unsafe_allow_html=True)
        c2.markdown(f"<div style='font-weight: 600; font-size: 13px;'>{nome}</div>"
                    f"<div style='font-size: 11px; color: #9CA3AF;'>+{tel}</div>", unsafe_allow_html=True)
        c3.markdown(f"<div style='font-size: 12px;'>{unid}</div>", unsafe_allow_html=True)
        c4.markdown(f"<div style='font-size: 12px;'>{tipo}</div>", unsafe_allow_html=True)
        c5.markdown(f"<div style='font-size: 12px;'>{STATUS_EMOJI.get(st_depois, st_depois)}</div>", unsafe_allow_html=True)
        c6.markdown(f"<div style='font-size: 12px; color: #4B5563;'>{obs}</div>", unsafe_allow_html=True)

    if len(df_f) > 200:
        st.caption(f"Mostrando 200 de {len(df_f)} registros. Use a busca pra refinar.")

    st.caption(f"⏱️ Cache 30s · {data.get('total', 0)} registros carregados de {data.get('total_planilha', 0)} na planilha")


# ============================================================================
# ABA 3: 🎁 PROGRAMA DE INDICAÇÕES
# ============================================================================

def tela_confirmacao_indicacoes():
    st.markdown("## 🎁 Programa de indicações")
    st.caption("Status dos convites enviados após a confirmação da sessão.")

    data = _apps_script_get("contexto")
    if _mostrar_erro_e_parar(data, "(carregando indicações)"):
        return

    df = _ctx_to_df(data)
    if df.empty or 'status' not in df.columns:
        st.info("Sem dados de indicações ainda.")
        return

    # Filtra primeiro só registros relacionados a indicação
    df_ind = df[df['status'].astype(str).str.startswith('indicacao_', na=False)].copy()

    if df_ind.empty:
        st.info("Nenhum convite de indicação enviado ainda.")
        return

    # Filtros de período + unidade (default Tudo pra ver o histórico completo)
    df_f = _filtros_periodo_unidade(df_ind, "timestamp_sp", "unidade", "indic", default_periodo="Tudo")

    if df_f.empty:
        st.info("Nenhum convite nos filtros selecionados.")
        return

    st_lower = df_f['status'].astype(str).str.lower()
    qtd_pendente = int((st_lower == 'indicacao_pendente').sum())
    qtd_aceita   = int((st_lower == 'indicacao_aceita').sum())
    qtd_recusada = int((st_lower == 'indicacao_recusada').sum())
    qtd_sem_resp = int((st_lower == 'indicacao_sem_resposta').sum())
    total = len(df_f)
    respondidas = qtd_aceita + qtd_recusada
    taxa_resp  = (respondidas / total * 100) if total else 0
    taxa_aceit = (qtd_aceita / respondidas * 100) if respondidas else 0

    st.markdown("")
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.markdown(_render_metric_card_local("✉️", total, "Convites enviados", "primary"), unsafe_allow_html=True)
    col_m2.markdown(_render_metric_card_local("📩", f"{taxa_resp:.0f}%", "Taxa de resposta",
                                              "blue", sub=f"{respondidas} de {total}"), unsafe_allow_html=True)
    col_m3.markdown(_render_metric_card_local("🎁", qtd_aceita, "Aceitaram", "green",
                                              sub=f"{taxa_aceit:.0f}% das respondidas"), unsafe_allow_html=True)
    col_m4.markdown(_render_metric_card_local("⏳", qtd_pendente, "Aguardando", "amber"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Funil + pizza
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.markdown("### Funil")
        fig = go.Figure(go.Funnel(
            y=["Convites enviados", "Responderam", "Aceitaram"],
            x=[total, respondidas, qtd_aceita],
            textinfo="value+percent initial",
            marker={"color": ["#5BC0BE", "#3b82f6", "#22c55e"]}
        ))
        fig.update_layout(height=300, margin=dict(t=20, b=20, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)

    with col_g2:
        st.markdown("### Distribuição")
        dist = pd.DataFrame({
            "Status": ["Aguardando", "Aceitas", "Recusadas", "Sem resposta"],
            "Qtd":    [qtd_pendente, qtd_aceita, qtd_recusada, qtd_sem_resp],
        })
        fig2 = px.pie(dist, values='Qtd', names='Status',
                      color_discrete_sequence=["#f59e0b", "#22c55e", "#9ca3af", "#e5e7eb"])
        fig2.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.markdown(f"### Lista · {len(df_f)} convite(s)")

    if 'timestamp_sp' in df_f.columns:
        df_f = df_f.sort_values('timestamp_sp', ascending=False)

    h1, h2, h3, h4, h5 = st.columns([1.8, 1.3, 0.9, 1.3, 1.4])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Status**")
    h5.markdown("**Quando**")
    st.markdown('<hr style="margin: 4px 0 8px 0;">', unsafe_allow_html=True)

    for _, row in df_f.head(100).iterrows():
        c1, c2, c3, c4, c5 = st.columns([1.8, 1.3, 0.9, 1.3, 1.4])
        nome = row.get('nome', '—') or '—'
        tel = str(row.get('telefone', '—'))
        unid = str(row.get('unidade', '—') or '—').replace('Mogi das Cruzes', 'Mogi')
        status = str(row.get('status', '—') or '—')
        try:
            quando = row['timestamp_sp'].strftime('%d/%m %H:%M')
        except Exception:
            quando = "—"

        c1.markdown(f"<div style='font-weight: 600;'>{nome}</div>", unsafe_allow_html=True)
        c2.markdown(f"<div style='font-size: 12px; color: #6B7280;'>+{tel}</div>", unsafe_allow_html=True)
        c3.write(unid)
        c4.write(STATUS_EMOJI.get(status, status))
        c5.markdown(f"<div style='font-size: 12px; color: #9CA3AF;'>{quando}</div>", unsafe_allow_html=True)

    if len(df_f) > 100:
        st.caption(f"Mostrando 100 de {len(df_f)} convites.")


# ============================================================================
# ABA 4: 📊 MÉTRICAS CONFIRMAÇÃO
# ============================================================================

def tela_confirmacao_metricas():
    st.markdown("## 📊 Métricas Robô Confirmação")
    st.caption("Visão geral filtrável por período e unidade — recalculada localmente conforme os filtros.")

    data = _apps_script_get("contexto")
    if _mostrar_erro_e_parar(data, "(carregando métricas)"):
        return

    df = _ctx_to_df(data)
    if df.empty:
        st.info("Sem dados ainda.")
        return

    # Filtros de período + unidade (default Tudo)
    df_f = _filtros_periodo_unidade(df, "timestamp_sp", "unidade", "metr", default_periodo="Tudo")

    if df_f.empty:
        st.info("Sem dados nos filtros selecionados.")
        return

    st.markdown("")

    # Calcula stats locais a partir do df filtrado
    st_lower = df_f['status'].astype(str).str.lower()
    total = len(df_f)
    confirmados  = int((st_lower == 'confirmado').sum())
    reagendados  = int((st_lower == 'reagendado').sum())
    cancelados   = int(st_lower.str.contains('cancelado', na=False).sum())
    aguardando   = int((st_lower == 'aguardando').sum())
    recepcao     = int(st_lower.str.contains('recep', na=False).sum())
    indic_pend   = int((st_lower == 'indicacao_pendente').sum())
    indic_aceit  = int((st_lower == 'indicacao_aceita').sum())
    indic_recus  = int((st_lower == 'indicacao_recusada').sum())
    indic_sresp  = int((st_lower == 'indicacao_sem_resposta').sum())

    # Taxa de confirmação considera só os "decididos" (confirmados + cancelados + reagendados)
    finalizados = confirmados + cancelados + reagendados
    taxa_conf = (confirmados / finalizados * 100) if finalizados else 0

    # Cards principais
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.markdown(_render_metric_card_local("📋", total, "Total no filtro", "primary"), unsafe_allow_html=True)
    col_m2.markdown(_render_metric_card_local("✅", f"{taxa_conf:.1f}%", "Taxa confirmação",
                                              "green", sub=f"{confirmados} de {finalizados}"), unsafe_allow_html=True)
    col_m3.markdown(_render_metric_card_local("⏳", aguardando, "Aguardando", "amber"), unsafe_allow_html=True)
    col_m4.markdown(_render_metric_card_local("🎁", indic_aceit, "Indic. aceitas", "purple",
                                              sub=f"{indic_pend} pendentes"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.divider()

    # Gráficos: por status + por unidade
    col_g1, col_g2 = st.columns(2)

    with col_g1:
        st.markdown("### Por status")
        por_status_dict = {
            "🟢 Confirmado": confirmados,
            "🔄 Reagendado": reagendados,
            "🔴 Cancelado": cancelados,
            "🟡 Aguardando": aguardando,
            "🟣 Recepção": recepcao,
            "🟠 Indic. pendente": indic_pend,
            "🎁 Indic. aceita": indic_aceit,
            "⚫ Indic. recusada": indic_recus,
            "⚪ Indic. sem resp": indic_sresp,
        }
        por_status_dict = {k: v for k, v in por_status_dict.items() if v > 0}
        if por_status_dict:
            df_st = pd.DataFrame([{"Status": k, "Qtd": v} for k, v in por_status_dict.items()]) \
                      .sort_values('Qtd', ascending=True)
            fig = px.bar(df_st, x='Qtd', y='Status', orientation='h',
                         color_discrete_sequence=["#5BC0BE"], text='Qtd')
            fig.update_traces(textposition='outside')
            fig.update_layout(height=380, margin=dict(t=10, b=10, l=10, r=30),
                              yaxis_title=None, xaxis_title=None)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados.")

    with col_g2:
        st.markdown("### Por unidade")
        if 'unidade' in df_f.columns:
            cnt_unid = df_f['unidade'].astype(str).str.replace('Mogi das Cruzes', 'Mogi').value_counts()
            if not cnt_unid.empty:
                df_un = pd.DataFrame({"Unidade": cnt_unid.index, "Qtd": cnt_unid.values})
                fig = px.pie(df_un, values='Qtd', names='Unidade',
                             color_discrete_sequence=["#5BC0BE", "#3b82f6", "#a3a3a3"])
                fig.update_layout(height=380, margin=dict(t=10, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Sem dados.")
        else:
            st.info("Sem dados de unidade.")

    st.divider()

    # Composição de outcomes (taxa)
    st.markdown("### 🎯 Composição de resultados (apenas disparos finalizados)")
    if finalizados > 0:
        outcomes = pd.DataFrame({
            "Resultado": ["Confirmados", "Reagendaram", "Cancelados"],
            "Qtd": [confirmados, reagendados, cancelados],
            "Pct": [
                confirmados/finalizados*100,
                reagendados/finalizados*100,
                cancelados/finalizados*100,
            ]
        })
        fig = go.Figure(go.Bar(
            x=outcomes['Qtd'], y=outcomes['Resultado'], orientation='h',
            text=[f"{q} ({p:.1f}%)" for q, p in zip(outcomes['Qtd'], outcomes['Pct'])],
            textposition='auto',
            marker_color=["#22c55e", "#3b82f6", "#ef4444"]
        ))
        fig.update_layout(height=200, margin=dict(t=10, b=10, l=10, r=10),
                          xaxis_title=None, yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Considera apenas os {finalizados} disparos finalizados. Aguardando/Recepção/Indicações ficam fora desta conta.")
    else:
        st.info("Ainda não há disparos finalizados nos filtros pra calcular.")

    st.caption(f"⏱️ Atualizado: {data.get('gerado_em', '—')[:19].replace('T', ' ')} · Cache 30s")
