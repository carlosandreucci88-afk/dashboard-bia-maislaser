"""
==============================================================================
ABA DISPARADOR AUTO — Visualização do estado da Bia v5 (modelo demolição)
==============================================================================

Substitui aba_historico_bia.py (que era do modelo FSM antigo com timeout 36h
+ auto-validação 30%). Agora o modelo é simples:

  1. Coordenadora marca campanha como AUTO na aba "⏳ Aguardando validação"
  2. Apps Script Filtro Webhook Bia (cron 10min) puxa o lote → INSERT bia_disparos
  3. Cron 1min dispara cortesia_v1 via Meta API
  4. Cliente clica botão → webhook → alerta Z-API pra recepção (status=RESPONDEU)

Esta aba é READ-ONLY. Lê SÓ do Supabase `bia_disparos` agrupado por campanha.
Não faz mutação. Pra marcar AUTO/MANUAL, usa a aba "Aguardando validação".

==============================================================================
3 seções (top-down):

  1. KPIs do período        — campanhas ativas, disparados, taxa de clique
  2. Em andamento           — campanhas com FILA ou DISPARADO sem resposta
  3. Concluídas              — campanhas com todos os disparos finalizados

Status individual em bia_disparos:
  • FILA              — esperando cron disparar
  • DISPARADO         — cortesia_v1 enviado, esperando resposta
  • RESPONDEU         — cliente clicou botão (AGENDAR ou SABER_MAIS)
  • SKIP_BASE         — telefone já é cliente da base, não disparado
  • ERRO_DISPARO      — falha no Meta API

Conceito de "concluída": uma campanha está concluída quando NENHUM disparo
está em FILA E os DISPARADO já têm pelo menos 24h (provavelmente não vão mais
clicar). É uma heurística — não há status terminal por campanha.

==============================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone

TZ_SP = timezone(timedelta(hours=-3))

# ============================================================================
# SUPABASE — lazy import (evita circular)
# ============================================================================

def _get_sb():
    from dashboard_maislaser import get_supabase
    return get_supabase()


# ============================================================================
# CARGA DE DADOS
# ============================================================================

@st.cache_data(ttl=20, show_spinner=False)
def _carregar_disparos(dias_atras=30):
    """Carrega bia_disparos dos últimos N dias. Default 30."""
    sb = _get_sb()
    try:
        data_limite = (datetime.now(TZ_SP) - timedelta(days=dias_atras)).isoformat()
        result = (sb.table("bia_disparos")
                  .select("id, telefone, nome_indicado, nome_cadastrante, "
                          "campanha_id, telefone_cadastrante, unidade, privacidade, "
                          "status, botao_clicado, fila_em, disparado_em, "
                          "respondeu_em, ultima_notif_recepcao, motivo_skip, "
                          "tentativas_envio")
                  .gte("fila_em", data_limite)
                  .order("fila_em", desc=True)
                  .limit(10000)
                  .execute())
        df = pd.DataFrame(result.data or [])
        if df.empty:
            return df
        for col in ('fila_em', 'disparado_em', 'respondeu_em', 'ultima_notif_recepcao'):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)
                try:
                    df[col + '_sp'] = df[col].dt.tz_convert(TZ_SP)
                except Exception:
                    df[col + '_sp'] = df[col]
        return df
    except Exception as e:
        st.error(f"Erro ao carregar bia_disparos: {e}")
        return pd.DataFrame()


def _agrupar_por_campanha(df_disp):
    """
    Agrupa disparos por campanha_id. Retorna DataFrame com 1 linha por campanha:
      - cadastrante (nome, telefone)
      - unidade, privacidade
      - contagens: total, fila, disparado, respondeu, skip_base, erro
      - taxa_clique (responderam / disparados, excluindo skip e erro)
      - primeiro_em, ultimo_em
      - estado_campanha: ATIVA / CONCLUIDA
    """
    if df_disp.empty:
        return pd.DataFrame()

    agora = pd.Timestamp.now(tz=TZ_SP)

    def _resumir(g):
        total = len(g)
        fila = int((g['status'] == 'FILA').sum())
        disparado = int((g['status'] == 'DISPARADO').sum())
        respondeu = int((g['status'] == 'RESPONDEU').sum())
        skip = int((g['status'] == 'SKIP_BASE').sum())
        erro = int((g['status'] == 'ERRO_DISPARO').sum())

        enviados = disparado + respondeu  # ignora SKIP_BASE e ERRO
        taxa = (respondeu / enviados * 100) if enviados > 0 else 0.0

        primeiro = g['fila_em_sp'].min() if 'fila_em_sp' in g.columns else None
        ultimo = g[['fila_em_sp', 'disparado_em_sp', 'respondeu_em_sp']].max(axis=1).max() \
                 if 'fila_em_sp' in g.columns else None

        # Heurística pra "concluída":
        # - 0 na fila E
        # - ou todos DISPARADO têm >24h (não vão mais clicar)
        # - ou tudo já é RESPONDEU/SKIP/ERRO
        disp_sem_resposta = g[g['status'] == 'DISPARADO']
        if fila == 0:
            if len(disp_sem_resposta) == 0:
                estado = 'CONCLUIDA'
            else:
                horas_min = (agora - disp_sem_resposta['disparado_em_sp'].max()).total_seconds() / 3600
                estado = 'CONCLUIDA' if horas_min >= 24 else 'ATIVA'
        else:
            estado = 'ATIVA'

        # pega 1ª linha pra extrair metadados do cadastrante
        primeira = g.iloc[0]
        return pd.Series({
            'campanha_id': g.name,  # 'campanha_id' virou o index do grupo no .apply()
            'nome_cadastrante': primeira.get('nome_cadastrante') or '—',
            'telefone_cadastrante': primeira.get('telefone_cadastrante') or '',
            'unidade': primeira.get('unidade') or '—',
            'privacidade': primeira.get('privacidade') or '',
            'total': total,
            'fila': fila,
            'disparado': disparado,
            'respondeu': respondeu,
            'skip_base': skip,
            'erro': erro,
            'enviados': enviados,
            'taxa_clique': taxa,
            'primeiro_em_sp': primeiro,
            'ultimo_em_sp': ultimo,
            'estado': estado,
        })

    grouped = df_disp.groupby('campanha_id', sort=False).apply(_resumir).reset_index(drop=True)
    grouped = grouped.sort_values('primeiro_em_sp', ascending=False)
    return grouped


# ============================================================================
# HELPERS DE UI
# ============================================================================

def _tempo_relativo(ts):
    if pd.isna(ts):
        return "—"
    try:
        delta = pd.Timestamp.now(tz=TZ_SP) - ts
        secs = delta.total_seconds()
        if secs < 60:
            return "agora"
        if secs < 3600:
            return f"{int(secs / 60)}min"
        if secs < 86400:
            return f"{int(secs / 3600)}h"
        return ts.strftime('%d/%m %H:%M')
    except Exception:
        return "—"


def _norm_unidade(u):
    if not isinstance(u, str) or not u.strip():
        return '—'
    ul = u.lower()
    if 'mogi' in ul:
        return 'Mogi'
    if 'suzano' in ul:
        return 'Suzano'
    return u


def _aplicar_filtro_unidade(df, unidade_sel):
    if df is None or df.empty or unidade_sel == 'Todas' or 'unidade' not in df.columns:
        return df
    target = unidade_sel.lower()
    return df[df['unidade'].astype(str).str.lower().str.contains(target, na=False)].copy()


def _filtro_unidade_global():
    key = "_disp_auto_unidade_persist"
    if key not in st.session_state:
        st.session_state[key] = "Todas"
    atual = st.session_state[key]
    st.markdown(
        "<div style='font-size: 14px; color: #6B7280; font-weight: 600; margin-bottom: 6px;'>"
        "📍 Filtrar por unidade</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    for col, label, valor in [(c1, "📌 Todas", "Todas"),
                               (c2, "📍 Mogi", "Mogi"),
                               (c3, "📍 Suzano", "Suzano")]:
        with col:
            if st.button(label, key=f"disp_auto_unid_{valor}",
                         type="primary" if atual == valor else "secondary",
                         use_container_width=True):
                st.session_state[key] = valor
                st.rerun()
    return st.session_state[key]


def _badge_estado(estado):
    if estado == 'ATIVA':
        return '<span style="background: #dbeafe; color: #1e40af; padding: 3px 10px; ' \
               'border-radius: 12px; font-weight: 700; font-size: 11px;">🚀 ATIVA</span>'
    return '<span style="background: #f3f4f6; color: #4b5563; padding: 3px 10px; ' \
           'border-radius: 12px; font-weight: 700; font-size: 11px;">✅ CONCLUÍDA</span>'


def _render_metric_card(icon, value, label, color="primary", sub=None):
    color_map = {'primary': '#5BC0BE', 'green': '#22c55e', 'red': '#ef4444',
                 'amber': '#f59e0b', 'blue': '#3b82f6', 'purple': '#8b5cf6'}
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
# DRILL-DOWN — detalhes de uma campanha
# ============================================================================

def _render_drilldown(camp_id, df_disp):
    if st.button("← Voltar pra lista", key="disp_auto_back"):
        st.session_state.pop('_disp_auto_drill_id', None)
        st.rerun()

    df_camp = df_disp[df_disp['campanha_id'] == camp_id].copy()
    if df_camp.empty:
        st.warning("Campanha não encontrada.")
        return

    primeira = df_camp.iloc[0]
    nome_cad = primeira.get('nome_cadastrante') or '—'
    unid = _norm_unidade(primeira.get('unidade'))
    priv = primeira.get('privacidade') or '—'

    st.markdown(f"## 🔍 Campanha — {nome_cad}")
    st.caption(f"📍 {unid} · 🔒 {priv} · ID: `{camp_id}`")

    total = len(df_camp)
    by_status = df_camp['status'].value_counts().to_dict()
    fila = by_status.get('FILA', 0)
    disp = by_status.get('DISPARADO', 0)
    resp = by_status.get('RESPONDEU', 0)
    skip = by_status.get('SKIP_BASE', 0)
    erro = by_status.get('ERRO_DISPARO', 0)
    enviados = disp + resp
    taxa = (resp / enviados * 100) if enviados > 0 else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📦 Total", total)
    c2.metric("⏳ Fila", fila)
    c3.metric("🚀 Disparados", disp + resp,
              delta=f"{resp} clicaram", delta_color="off" if resp == 0 else "normal")
    c4.metric("📊 Taxa clique", f"{taxa:.1f}%")
    c5.metric("⏭️ Skip / ❌ Erro", f"{skip} / {erro}")

    st.divider()

    st.markdown("### 📋 Disparos individuais")

    df_disp_view = df_camp.copy()
    df_disp_view['Telefone'] = '+' + df_disp_view['telefone'].astype(str)
    df_disp_view['Indicado'] = df_disp_view['nome_indicado'].fillna('(sem nome)')

    def _emoji_status(s):
        return {'FILA': '⏳', 'DISPARADO': '🚀', 'RESPONDEU': '💬',
                'SKIP_BASE': '⏭️', 'ERRO_DISPARO': '❌'}.get(s, '❓')

    df_disp_view['Status'] = df_disp_view['status'].apply(lambda s: f"{_emoji_status(s)} {s}")
    df_disp_view['Botão'] = df_disp_view['botao_clicado'].fillna('—')

    for col_dt, col_label in [('disparado_em_sp', '🚀 Disparado'),
                                ('respondeu_em_sp', '💬 Respondeu'),
                                ('ultima_notif_recepcao_sp', '🔔 Recepção avisada')]:
        if col_dt in df_disp_view.columns:
            df_disp_view[col_label] = df_disp_view[col_dt].apply(
                lambda d: d.strftime('%d/%m %H:%M') if pd.notna(d) else '—'
            )

    cols_show = ['Indicado', 'Telefone', 'Status', 'Botão',
                 '🚀 Disparado', '💬 Respondeu', '🔔 Recepção avisada']
    cols_exist = [c for c in cols_show if c in df_disp_view.columns]
    st.dataframe(df_disp_view[cols_exist].reset_index(drop=True),
                 use_container_width=True, hide_index=True, height=500)
    st.caption(f"📋 {len(df_disp_view)} disparo(s)")


# ============================================================================
# RESUMO — cards + lista de campanhas
# ============================================================================

def _render_resumo(df_camp, df_disp_raw):
    if df_camp.empty:
        st.info("📭 Nenhuma campanha no período.")
        return

    # KPIs
    qtd_total = len(df_camp)
    qtd_ativas = int((df_camp['estado'] == 'ATIVA').sum())
    qtd_concl = int((df_camp['estado'] == 'CONCLUIDA').sum())
    total_disp_geral = int(df_camp['enviados'].sum())
    total_resp_geral = int(df_camp['respondeu'].sum())
    total_fila = int(df_camp['fila'].sum())
    taxa_geral = (total_resp_geral / total_disp_geral * 100) if total_disp_geral > 0 else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_render_metric_card("📦", qtd_total, "Campanhas", "primary"), unsafe_allow_html=True)
    c2.markdown(_render_metric_card("🚀", qtd_ativas, "Ativas", "blue",
                                    sub=f"{total_fila} na fila"), unsafe_allow_html=True)
    c3.markdown(_render_metric_card("✅", qtd_concl, "Concluídas", "green"), unsafe_allow_html=True)
    c4.markdown(_render_metric_card("💬", total_resp_geral, "Cliques", "amber",
                                    sub=f"de {total_disp_geral} enviados"), unsafe_allow_html=True)
    c5.markdown(_render_metric_card("📊", f"{taxa_geral:.1f}%", "Taxa clique geral",
                                    "purple"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.divider()

    # Filtros secundários
    if "_disp_auto_estado_persist" not in st.session_state:
        st.session_state["_disp_auto_estado_persist"] = "Todas"
    if "_disp_auto_busca_persist" not in st.session_state:
        st.session_state["_disp_auto_busca_persist"] = ""

    opcoes_estado = ["Todas", "🚀 Ativas", "✅ Concluídas"]
    try:
        idx = opcoes_estado.index(st.session_state["_disp_auto_estado_persist"])
    except ValueError:
        idx = 0

    cf1, cf2 = st.columns([2, 3])
    with cf1:
        estado_sel = st.selectbox("Estado:", opcoes_estado, index=idx,
                                   key="disp_auto_estado_sel")
    with cf2:
        busca = st.text_input("🔍 Buscar cadastrante (nome ou telefone):",
                              value=st.session_state["_disp_auto_busca_persist"],
                              key="disp_auto_busca")

    st.session_state["_disp_auto_estado_persist"] = estado_sel
    st.session_state["_disp_auto_busca_persist"] = busca

    df_f = df_camp.copy()
    if estado_sel == "🚀 Ativas":
        df_f = df_f[df_f['estado'] == 'ATIVA']
    elif estado_sel == "✅ Concluídas":
        df_f = df_f[df_f['estado'] == 'CONCLUIDA']

    if busca.strip():
        b = busca.strip().lower()
        mask = (df_f['nome_cadastrante'].astype(str).str.lower().str.contains(b, na=False) |
                df_f['telefone_cadastrante'].astype(str).str.contains(b, na=False))
        df_f = df_f[mask]

    st.caption(f"📍 Mostrando **{len(df_f)}** de {len(df_camp)} campanha(s)")

    if df_f.empty:
        st.info("Nenhuma campanha com esses filtros.")
        return

    # CSS dos cards
    st.markdown("""
    <style>
    .camp-card {
        padding: 14px 18px;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        margin-bottom: 10px;
        background: white;
        transition: all 0.15s ease;
    }
    .camp-card:hover { border-color: #5BC0BE; box-shadow: 0 2px 8px rgba(91,192,190,0.15); }
    .camp-card-concl { background: #fafafa; opacity: 0.92; }
    .progress-bar { background: #e5e7eb; border-radius: 6px; height: 16px; overflow: hidden; margin-top: 8px; }
    .progress-fill-ativa { background: linear-gradient(90deg, #5BC0BE 0%, #3D9991 100%); height: 100%; }
    .progress-fill-concl { background: linear-gradient(90deg, #94a3b8 0%, #64748b 100%); height: 100%; }
    </style>
    """, unsafe_allow_html=True)

    # Cards das campanhas
    for _, row in df_f.head(100).iterrows():
        nome = row['nome_cadastrante']
        tel_cad = row['telefone_cadastrante']
        unid = _norm_unidade(row['unidade'])
        total = row['total']
        fila = row['fila']
        disp = row['disparado']
        resp = row['respondeu']
        skip = row['skip_base']
        erro = row['erro']
        enviados = row['enviados']
        taxa = row['taxa_clique']

        pct_enviado = int((enviados / max(total - skip, 1)) * 100)
        pct_clique = int((resp / max(enviados, 1)) * 100)

        primeiro_str = _tempo_relativo(row['primeiro_em_sp'])
        card_class = "camp-card camp-card-concl" if row['estado'] == 'CONCLUIDA' else "camp-card"
        fill_class = "progress-fill-concl" if row['estado'] == 'CONCLUIDA' else "progress-fill-ativa"

        tel_cad_str = f"+{tel_cad}" if tel_cad else ""

        html = (
            f'<div class="{card_class}">'
            f'<div style="display: flex; justify-content: space-between; align-items: flex-start;">'
            f'<div>'
            f'<strong style="font-size: 15px;">{nome}</strong>'
            f'<span style="color: #6b7280; font-size: 13px;"> · 📱 {tel_cad_str} · 📍 {unid}</span>'
            f'</div>'
            f'<div>{_badge_estado(row["estado"])}</div>'
            f'</div>'
            f'<div style="margin-top: 6px; color: #6b7280; font-size: 12px;">'
            f'🕒 começou há {primeiro_str}'
            f'</div>'
            f'<div style="margin-top: 10px;">'
            f'<div style="display: flex; justify-content: space-between; font-size: 12px; color: #374151;">'
            f'<span>📨 {enviados}/{total - skip} enviados '
            f'· ⏳ {fila} fila '
            f'· 💬 {resp} clicaram '
            f'· ⏭️ {skip} skip'
            f'{f" · ❌ {erro} erro" if erro > 0 else ""}'
            f'</span>'
            f'<strong style="color: #5BC0BE;">{taxa:.1f}% clique</strong>'
            f'</div>'
            f'<div class="progress-bar">'
            f'<div class="{fill_class}" style="width: {pct_enviado}%;"></div>'
            f'</div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)

        col_btn, _ = st.columns([1, 4])
        with col_btn:
            if st.button(f"🔍 Ver {total} disparos",
                         key=f"disp_drill_{row['campanha_id']}",
                         use_container_width=True):
                st.session_state['_disp_auto_drill_id'] = row['campanha_id']
                st.rerun()

    if len(df_f) > 100:
        st.caption(f"Mostrando 100 de {len(df_f)}. Use os filtros pra refinar.")


# ============================================================================
# ENTRYPOINT
# ============================================================================

def render_aba_disparador_auto():
    st.markdown("## 🤖 Disparador AUTO")
    st.caption(
        "Visão em tempo real do que a Bia v5 está disparando. Pra marcar uma "
        "campanha como AUTO, use a aba **⏳ Aguardando validação**."
    )

    # Filtro global de unidade
    unidade_sel = _filtro_unidade_global()
    st.markdown(
        '<hr style="margin: 12px 0 18px 0; border: none; border-top: 1px solid #E5E7EB;">',
        unsafe_allow_html=True,
    )

    # Carga e agrupamento
    df_disp_raw = _carregar_disparos(dias_atras=30)
    if df_disp_raw.empty:
        st.info(
            "📭 **Nenhum disparo nos últimos 30 dias.**\n\n"
            "Quando a coordenadora marcar uma campanha como **AUTO** na aba "
            "*Aguardando validação*, a Bia vai puxar o lote (cron 10min) e "
            "disparar 1 mensagem por minuto. Vai aparecer aqui em tempo real."
        )
        return

    df_disp_filt = _aplicar_filtro_unidade(df_disp_raw, unidade_sel)
    df_camp = _agrupar_por_campanha(df_disp_filt)

    # Drill-down ativo?
    drill_id = st.session_state.get('_disp_auto_drill_id')
    if drill_id:
        _render_drilldown(drill_id, df_disp_filt)
    else:
        _render_resumo(df_camp, df_disp_filt)
