"""
Aba 📋 Histórico de Disparos — lista cronológica dos disparos realizados.
Separação Mogi / Suzano. Campo de observação editável.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================
# DADOS
# ============================================================

@st.cache_resource
def _get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=15)
def _carregar_historico():
    sb = _get_supabase()
    try:
        result = (
            sb.table("disparos_historico")
            .select("*")
            .order("criado_em", desc=True)
            .limit(200)
            .execute()
        )
        df = pd.DataFrame(result.data)
        if not df.empty:
            df["criado_em"] = pd.to_datetime(df["criado_em"], format="ISO8601")
        return df
    except Exception as e:
        st.error(f"Erro ao carregar histórico: {e}")
        return pd.DataFrame()


def _salvar_observacao(registro_id: int, texto: str):
    sb = _get_supabase()
    try:
        sb.table("disparos_historico").update(
            {"observacao": texto}
        ).eq("id", registro_id).execute()
        _carregar_historico.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar observação: {e}")
        return False


# ============================================================
# HELPERS
# ============================================================

def _safe_str(valor) -> str:
    """
    Converte valor pra string segura, tratando NaN/None.
    Bug v6.14.1 corrigido (24/06/2026): Supabase retorna NULL como np.nan
    no pandas. `str(np.nan) == "nan"` (literal), e `np.nan or ""` retorna
    np.nan porque NaN é TRUTHY em Python (é float não-zero). Por isso o
    dashboard renderizava "nan" em vez de string vazia.
    """
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(valor).strip()
    if s.lower() == "nan" or s.lower() == "none":
        return ""
    return s


# ============================================================
# RENDER
# ============================================================

def render_aba_historico_disparos():
    st.markdown("## 📋 Histórico de Disparos")
    st.caption("Registro de cada disparo realizado pelo dashboard.")

    df = _carregar_historico()

    if df.empty:
        st.info("Nenhum disparo registrado ainda. Quando você fizer o primeiro disparo com o fix aplicado, ele aparecerá aqui.")
        return

    # ─── Filtro Mogi / Suzano ───
    if "hist_unidade_btn" not in st.session_state:
        st.session_state["hist_unidade_btn"] = "Todas"

    cnt_todas = len(df)
    cnt_mogi = len(df[df["unidade"].str.contains("Mogi", case=False, na=False)])
    cnt_suzano = len(df[df["unidade"].str.contains("Suzano", case=False, na=False)])

    btn1, btn2, btn3, _ = st.columns([1.2, 1.6, 1.2, 4])

    with btn1:
        is_todas = st.session_state["hist_unidade_btn"] == "Todas"
        if st.button(
            f"🏢 Todas ({cnt_todas})",
            type="primary" if is_todas else "secondary",
            use_container_width=True,
            key="hist_btn_todas",
        ):
            st.session_state["hist_unidade_btn"] = "Todas"
            st.rerun()

    with btn2:
        is_mogi = st.session_state["hist_unidade_btn"] == "Mogi"
        if st.button(
            f"📍 Mogi das Cruzes ({cnt_mogi})",
            type="primary" if is_mogi else "secondary",
            use_container_width=True,
            key="hist_btn_mogi",
        ):
            st.session_state["hist_unidade_btn"] = "Mogi"
            st.rerun()

    with btn3:
        is_suzano = st.session_state["hist_unidade_btn"] == "Suzano"
        if st.button(
            f"📍 Suzano ({cnt_suzano})",
            type="primary" if is_suzano else "secondary",
            use_container_width=True,
            key="hist_btn_suzano",
        ):
            st.session_state["hist_unidade_btn"] = "Suzano"
            st.rerun()

    filtro = st.session_state["hist_unidade_btn"]
    df_filt = df.copy()
    if filtro == "Mogi":
        df_filt = df_filt[df_filt["unidade"].str.contains("Mogi", case=False, na=False)]
    elif filtro == "Suzano":
        df_filt = df_filt[df_filt["unidade"].str.contains("Suzano", case=False, na=False)]

    if df_filt.empty:
        st.info("Nenhum disparo pra essa unidade.")
        return

    st.markdown("")
    st.markdown(f"### {len(df_filt)} disparo(s)")

    # ═════════════════════════════════════════════════════════════════
    # PAGINAÇÃO (v6.15, 14/07/2026) — mesmo padrão da aba do Pós
    # ═════════════════════════════════════════════════════════════════
    # Detecta mudança de filtro pra resetar página pra 1
    filtros_hash = f"{filtro}|{len(df_filt)}"
    if st.session_state.get("hist_ag_filtros_hash") != filtros_hash:
        st.session_state.hist_ag_pag_atual = 1
        st.session_state.hist_ag_filtros_hash = filtros_hash

    total_registros = len(df_filt)

    col_tam, col_prev, col_pag, col_next, col_info = st.columns([1.5, 1, 1.5, 1, 3])

    with col_tam:
        tam_pag = st.selectbox(
            "Por página",
            [10, 20, 50, 100],
            index=1,
            key="hist_ag_tam_pag",
        )

    total_paginas = max(1, (total_registros + tam_pag - 1) // tam_pag)

    if "hist_ag_pag_atual" not in st.session_state:
        st.session_state.hist_ag_pag_atual = 1
    if st.session_state.hist_ag_pag_atual > total_paginas:
        st.session_state.hist_ag_pag_atual = total_paginas

    with col_prev:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        if st.button("◀ Anterior", use_container_width=True, key="hist_ag_btn_prev",
                     disabled=(st.session_state.hist_ag_pag_atual <= 1)):
            st.session_state.hist_ag_pag_atual -= 1
            st.rerun()

    with col_pag:
        pag_input = st.number_input(
            f"Página (1-{total_paginas})",
            min_value=1,
            max_value=total_paginas,
            value=st.session_state.hist_ag_pag_atual,
            step=1,
            key="hist_ag_pag_input",
        )
        if pag_input != st.session_state.hist_ag_pag_atual:
            st.session_state.hist_ag_pag_atual = pag_input
            st.rerun()

    with col_next:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        if st.button("Próxima ▶", use_container_width=True, key="hist_ag_btn_next",
                     disabled=(st.session_state.hist_ag_pag_atual >= total_paginas)):
            st.session_state.hist_ag_pag_atual += 1
            st.rerun()

    # Fatia o df pela página atual
    inicio = (st.session_state.hist_ag_pag_atual - 1) * tam_pag
    fim = inicio + tam_pag
    df_pag = df_filt.iloc[inicio:fim]

    with col_info:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        st.caption(
            f"Exibindo **{inicio + 1}–{min(fim, total_registros)}** "
            f"de **{total_registros}** · Página **{st.session_state.hist_ag_pag_atual}/{total_paginas}**"
        )

    st.divider()

    # ─── Lista ───
    for _, row in df_pag.iterrows():
        reg_id = int(row["id"])

        # Data formatada
        try:
            dt = row["criado_em"]
            if hasattr(dt, "tz_convert"):
                dt = dt.tz_convert(TZ_SP)
            elif hasattr(dt, "astimezone"):
                dt = dt.astimezone(TZ_SP)
            data_str = dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            data_str = str(row["criado_em"])[:16]

        # Badge de status
        # 🆕 v6.14.1 (24/06/2026): lógica de classificação corrigida.
        # ANTES: disparo INTERROMPIDO (0 enviados de N clientes) caía em
        # falhas==0 && erros==0 → era classificado como "✅ Sucesso total".
        # AGORA: checa observação por INTERROMPIDO/FALHOU primeiro, depois
        # checa se whatsapp_ok < total_clientes (disparo parcial).
        falhas = int(row.get("falhas_contexto") or 0)
        erros = int(row.get("erros_envio") or 0)
        total_chk = int(row.get("total_clientes") or 0)
        wpp_chk = int(row.get("whatsapp_ok") or 0)
        obs_chk = _safe_str(row.get("observacao")).upper()

        if "INTERROMPIDO" in obs_chk or "FALHOU" in obs_chk:
            badge = '<span class="badge-amber">❌ Interrompido</span>'
        elif total_chk > 0 and wpp_chk == 0:
            badge = '<span class="badge-amber">❌ Nenhum envio realizado</span>'
        elif total_chk > 0 and wpp_chk < total_chk:
            faltam = total_chk - wpp_chk
            badge = f'<span class="badge-alerta">⚠️ Parcial — {faltam} não enviado(s)</span>'
        elif falhas == 0 and erros == 0:
            badge = '<span class="badge-ok">✅ Sucesso total</span>'
        elif falhas > 0:
            badge = f'<span class="badge-alerta">⚠️ {falhas} contexto(s) perdido(s)</span>'
        elif erros > 0:
            badge = f'<span class="badge-amber">❌ {erros} erro(s) de envio</span>'
        else:
            badge = '<span class="badge-neutral">—</span>'

        # Unidade badge
        unidade = str(row.get("unidade") or "—")
        if "Mogi" in unidade:
            unid_badge = '<span class="badge-info">📍 Mogi</span>'
        elif "Suzano" in unidade:
            unid_badge = '<span class="badge-purple">📍 Suzano</span>'
        else:
            unid_badge = f'<span class="badge-neutral">{unidade}</span>'

        total = int(row.get("total_clientes") or 0)
        wpp_ok = int(row.get("whatsapp_ok") or 0)
        ctx_ok = int(row.get("contexto_ok") or 0)
        arquivo = _safe_str(row.get("arquivo")) or "—"
        data_sess = _safe_str(row.get("data_sessoes")) or "—"

        # Linha principal
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 4px;">
                <span style="font-weight: 700; color: var(--text); font-size: 15px;">{data_str}</span>
                {unid_badge}
                {badge}
            </div>
            <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 2px;">
                📄 {arquivo} · 📅 Sessões: {data_sess}
            </div>
            <div style="font-size: 13px; color: var(--text-secondary);">
                👥 {total} clientes · 📤 {wpp_ok} enviados · 💾 {ctx_ok} contextos
                {f' · ⚠️ {falhas} falha(s)' if falhas > 0 else ''}
                {f' · ❌ {erros} erro(s)' if erros > 0 else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Clientes que falharam (se houver)
        # 🆕 v6.14.1 — usa _safe_str pra eliminar "nan" quando valor é NULL no Supabase
        clientes_falha = _safe_str(row.get("clientes_falha"))
        if clientes_falha:
            st.caption(f"⚠️ Contexto perdido: {clientes_falha}")

        # Observação editável
        obs_atual = _safe_str(row.get("observacao"))
        col_obs, col_btn = st.columns([5, 1])
        with col_obs:
            nova_obs = st.text_input(
                "Observação",
                value=obs_atual,
                key=f"hist_obs_{reg_id}",
                label_visibility="collapsed",
                placeholder="Adicionar observação...",
            )
        with col_btn:
            if nova_obs != obs_atual:
                if st.button("💾", key=f"hist_save_{reg_id}", use_container_width=True):
                    if _salvar_observacao(reg_id, nova_obs):
                        st.toast("Observação salva!", icon="✅")
                        st.rerun()

        st.divider()
