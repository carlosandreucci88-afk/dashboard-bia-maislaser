"""
==============================================================================
ROBÔ PÓS-ATENDIMENTO — Aba "⚙️ Configurações"
==============================================================================
v1.0 (04/07/2026)

Edita tudo pela dashboard, sem tocar no Supabase:
    - Coordenadora Mogi (nome + telefone)
    - Coordenadora Suzano (nome + telefone)
    - Link Google Reviews Mogi
    - Link Google Reviews Suzano
    - Código do cupom
    - Kill switch (pos_habilitado)
    - Janela horário (inicio, fim)

Cada seção tem seu próprio botão salvar — se falhar, isola.
Limpa cache das outras abas depois de salvar.
==============================================================================
"""

import streamlit as st
import re
from supabase import create_client, Client

# ============================================================================
# CONEXÃO
# ============================================================================
@st.cache_resource
def _get_sb() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=10, show_spinner=False)
def _ler_config_atual() -> dict:
    try:
        sb = _get_sb()
        r = sb.table("configuracoes").select(
            "pos_habilitado,pos_janela_hora_inicio,pos_janela_hora_fim,"
            "pos_coord_mogi_telefone,pos_coord_mogi_nome,"
            "pos_coord_suzano_telefone,pos_coord_suzano_nome,"
            "pos_google_review_mogi,pos_google_review_suzano,"
            "pos_codigo_cupom"
        ).eq("id", 1).execute()
        if r.data:
            return r.data[0]
    except Exception as e:
        st.error(f"⚠️ Erro ao ler config: {e}")
    return {}


def _salvar(campos: dict) -> tuple:
    """Retorna (sucesso: bool, msg: str)."""
    try:
        sb = _get_sb()
        sb.table("configuracoes").update(campos).eq("id", 1).execute()
        # Limpa cache pra outras abas verem valores novos
        st.cache_data.clear()
        return True, "✅ Salvo!"
    except Exception as e:
        return False, f"❌ Falhou: {e}"


def _limpar_telefone(tel: str) -> str:
    """Só dígitos."""
    return re.sub(r"\D", "", str(tel or ""))


def _valida_telefone_br(tel: str) -> bool:
    """Formato aceito: 55 + DDD + 9 dígitos = 13 chars."""
    limpo = _limpar_telefone(tel)
    return len(limpo) >= 12 and len(limpo) <= 13 and limpo.startswith("55")


# ============================================================================
# UI
# ============================================================================

def render_aba_pos_config():
    st.markdown("## ⚙️ Configurações do Robô Pós-atendimento")
    st.caption("Ajustes aplicam imediatamente. Não precisa reiniciar nada.")

    cfg = _ler_config_atual()
    if not cfg:
        st.error("⚠️ Não consegui ler as configurações. Verifique conexão Supabase.")
        return

    # ═════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — KILL SWITCH
    # ═════════════════════════════════════════════════════════════════
    st.markdown("### 🔴 Kill switch")

    col_kill_a, col_kill_b = st.columns([1, 3])
    with col_kill_a:
        atual = bool(cfg.get("pos_habilitado", True))
        emoji_status = "🟢 LIGADO" if atual else "🔴 DESLIGADO"
        st.markdown(f"**Status atual:** {emoji_status}")

    with col_kill_b:
        if atual:
            if st.button("🔴 Desabilitar robô agora", type="secondary", use_container_width=True, key="cfg_kill_off"):
                ok, msg = _salvar({"pos_habilitado": False})
                if ok:
                    st.success("✅ Robô DESABILITADO. Novos disparos e webhooks estão pausados.")
                    st.rerun()
                else:
                    st.error(msg)
        else:
            if st.button("🟢 Habilitar robô", type="primary", use_container_width=True, key="cfg_kill_on"):
                ok, msg = _salvar({"pos_habilitado": True})
                if ok:
                    st.success("✅ Robô HABILITADO.")
                    st.rerun()
                else:
                    st.error(msg)

    st.caption("Quando desligado: disparos falham e webhook ignora mensagens recebidas.")

    st.divider()

    # ═════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — COORDENADORAS TÉCNICAS
    # ═════════════════════════════════════════════════════════════════
    st.markdown("### 👩‍⚕️ Coordenadoras técnicas")
    st.caption("Elas recebem os alertas de problemas, resultado ruim e pedidos de cupom.")

    col_m, col_s = st.columns(2)

    with col_m:
        st.markdown("**📍 Mogi das Cruzes**")
        mogi_nome = st.text_input(
            "Nome",
            value=cfg.get("pos_coord_mogi_nome", "Coordenadora Mogi"),
            key="cfg_coord_mogi_nome"
        )
        mogi_tel = st.text_input(
            "Telefone (formato: 5511974485859)",
            value=cfg.get("pos_coord_mogi_telefone", ""),
            key="cfg_coord_mogi_tel"
        )
        if st.button("💾 Salvar Mogi", type="primary", use_container_width=True, key="cfg_save_mogi"):
            tel_limpo = _limpar_telefone(mogi_tel)
            if not _valida_telefone_br(tel_limpo):
                st.error("❌ Telefone inválido. Precisa começar com 55 e ter 12-13 dígitos totais.")
            else:
                ok, msg = _salvar({
                    "pos_coord_mogi_nome": mogi_nome.strip() or "Coordenadora Mogi",
                    "pos_coord_mogi_telefone": tel_limpo,
                })
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    with col_s:
        st.markdown("**📍 Suzano**")
        suz_nome = st.text_input(
            "Nome",
            value=cfg.get("pos_coord_suzano_nome", "Coordenadora Suzano"),
            key="cfg_coord_suz_nome"
        )
        suz_tel = st.text_input(
            "Telefone (formato: 5511913194989)",
            value=cfg.get("pos_coord_suzano_telefone", ""),
            key="cfg_coord_suz_tel"
        )
        if st.button("💾 Salvar Suzano", type="primary", use_container_width=True, key="cfg_save_suz"):
            tel_limpo = _limpar_telefone(suz_tel)
            if not _valida_telefone_br(tel_limpo):
                st.error("❌ Telefone inválido. Precisa começar com 55 e ter 12-13 dígitos totais.")
            else:
                ok, msg = _salvar({
                    "pos_coord_suzano_nome": suz_nome.strip() or "Coordenadora Suzano",
                    "pos_coord_suzano_telefone": tel_limpo,
                })
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.divider()

    # ═════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — LINKS GOOGLE REVIEWS
    # ═════════════════════════════════════════════════════════════════
    st.markdown("### 🔗 Links do Google Reviews")
    st.caption("Enviados na mensagem após cliente clicar em '🌟 Tudo ótimo'.")

    col_lm, col_ls = st.columns(2)

    with col_lm:
        st.markdown("**📍 Mogi das Cruzes**")
        link_mogi = st.text_input(
            "URL",
            value=cfg.get("pos_google_review_mogi", ""),
            key="cfg_link_mogi",
            placeholder="https://g.page/r/..."
        )
        if st.button("💾 Salvar link Mogi", type="primary", use_container_width=True, key="cfg_save_link_mogi"):
            if not link_mogi.strip().startswith("http"):
                st.error("❌ URL inválida. Precisa começar com https://")
            else:
                ok, msg = _salvar({"pos_google_review_mogi": link_mogi.strip()})
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    with col_ls:
        st.markdown("**📍 Suzano**")
        link_suz = st.text_input(
            "URL",
            value=cfg.get("pos_google_review_suzano", ""),
            key="cfg_link_suz",
            placeholder="https://g.page/r/..."
        )
        if st.button("💾 Salvar link Suzano", type="primary", use_container_width=True, key="cfg_save_link_suz"):
            if not link_suz.strip().startswith("http"):
                st.error("❌ URL inválida. Precisa começar com https://")
            else:
                ok, msg = _salvar({"pos_google_review_suzano": link_suz.strip()})
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.divider()

    # ═════════════════════════════════════════════════════════════════
    # SEÇÃO 4 — CUPOM
    # ═════════════════════════════════════════════════════════════════
    st.markdown("### 🎁 Código do cupom")
    st.caption("Código usado nas mensagens de 'Tudo ótimo' e 'Deixar pra próxima'.")

    col_c1, col_c2 = st.columns([3, 1])
    with col_c1:
        cupom = st.text_input(
            "Código",
            value=cfg.get("pos_codigo_cupom", "CUPOMPOS70%OFF"),
            key="cfg_cupom",
            placeholder="CUPOMPOS70%OFF"
        )
    with col_c2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 Salvar cupom", type="primary", use_container_width=True, key="cfg_save_cupom"):
            if not cupom.strip():
                st.error("❌ Código não pode ficar vazio.")
            else:
                ok, msg = _salvar({"pos_codigo_cupom": cupom.strip()})
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.divider()

    # ═════════════════════════════════════════════════════════════════
    # SEÇÃO 5 — JANELA HORÁRIO
    # ═════════════════════════════════════════════════════════════════
    st.markdown("### 🕐 Janela de horário permitido")
    st.caption("Disparos fora dessa janela ficam bloqueados. Webhook responde 24/7 (não é afetado).")

    col_h1, col_h2, col_h3 = st.columns([2, 2, 2])
    with col_h1:
        h_ini = st.number_input(
            "Hora início",
            min_value=0, max_value=23,
            value=int(cfg.get("pos_janela_hora_inicio", 8)),
            step=1,
            key="cfg_hora_ini"
        )
    with col_h2:
        h_fim = st.number_input(
            "Hora fim",
            min_value=1, max_value=24,
            value=int(cfg.get("pos_janela_hora_fim", 19)),
            step=1,
            key="cfg_hora_fim"
        )
    with col_h3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 Salvar horários", type="primary", use_container_width=True, key="cfg_save_horas"):
            if h_ini >= h_fim:
                st.error("❌ Hora início deve ser menor que hora fim.")
            else:
                ok, msg = _salvar({
                    "pos_janela_hora_inicio": int(h_ini),
                    "pos_janela_hora_fim": int(h_fim),
                })
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.caption(f"Atualmente: **{cfg.get('pos_janela_hora_inicio', 8)}h - {cfg.get('pos_janela_hora_fim', 19)}h**")

    st.divider()

    # ═════════════════════════════════════════════════════════════════
    # RESUMO ATUAL
    # ═════════════════════════════════════════════════════════════════
    with st.expander("📄 Ver configuração atual completa (JSON)"):
        st.json(cfg)
