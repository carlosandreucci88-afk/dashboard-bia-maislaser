"""
==============================================================================
ROBÔ MARKETING — Aba "📤 Disparos MKT" — VERSÃO MÍNIMA DE DIAGNÓSTICO
==============================================================================
v1.2-diag (07/08/2026)

PROPÓSITO: versão ultra-simplificada só pra testar se o problema é
o arquivo aba_marketing.py cheio ou outra coisa.

Se com este arquivo o dashboard voltar a abrir → problema era um import
ou algo pesado no aba_marketing.py completo.

Se ainda ficar branco → problema é infra Streamlit, não código nosso.
==============================================================================
"""

import streamlit as st


def render_aba_marketing():
    """Placeholder mínimo — sem imports pesados."""
    st.title("📤 Disparos MKT")
    st.warning("⚠️ Versão de diagnóstico — funcionalidade completa temporariamente indisponível.")
    st.info(
        "Se você está vendo esta mensagem, significa que o Streamlit Cloud "
        "conseguiu carregar o dashboard. O sistema completo do Robô Marketing "
        "será restaurado após validação."
    )
    st.caption("v1.2-diag — 07/08/2026")
