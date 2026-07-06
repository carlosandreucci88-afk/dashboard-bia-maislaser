"""
==============================================================================
utils_erros.py — Módulo helper pra capturar e registrar erros no Supabase
==============================================================================
v1.0 (06/07/2026)

USO NO STREAMLIT:

    # Opção 1: função direta (pra try/except manual)
    from utils_erros import registrar_erro

    try:
        # ... código ...
    except Exception as e:
        registrar_erro(
            robo="pos_atendimento",
            origem="dashboard",
            modulo="aba_pos_disparar.py::_executar_disparo",
            exc=e,
            contexto={"cliente_id": 47, "telefone": row["telefone"]}
        )
        raise  # re-lança se quiser mostrar na UI

    # Opção 2: decorator (pra função inteira)
    from utils_erros import capturar_erros

    @capturar_erros(robo="pos_atendimento", origem="dashboard", severidade="error")
    def minha_funcao(x, y):
        return 1/0  # se der erro, registra automaticamente

    # Opção 3: context manager (pra bloco de código)
    from utils_erros import erro_scope

    with erro_scope(robo="pos_atendimento", origem="dashboard",
                    contexto={"batch_size": 25}):
        # se qualquer coisa aqui der erro, registra e re-lança
        _fazer_algo_perigoso()
==============================================================================
"""

import traceback
import functools
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import streamlit as st
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# CONEXÃO (usa mesma pattern do resto do dashboard)
# ============================================================================
@st.cache_resource
def _get_sb_erros() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# ============================================================================
# FUNÇÃO PRINCIPAL — registra 1 erro
# ============================================================================
def registrar_erro(
    robo: str,
    origem: str,
    mensagem: Optional[str] = None,
    severidade: str = "error",
    modulo: Optional[str] = None,
    exc: Optional[BaseException] = None,
    contexto: Optional[Dict[str, Any]] = None,
    telefone_cliente: Optional[str] = None,
    unidade: Optional[str] = None,
    silencioso: bool = True,
) -> Optional[int]:
    """
    Registra um erro na tabela sistema_erros.

    Args:
        robo:       'pos_atendimento' | 'agenda' | 'bia' | 'dashboard' | 'sistema'
        origem:     'dashboard' | 'apps_script' | 'trigger' | 'webhook'
        mensagem:   Mensagem descritiva. Se None e exc fornecida, usa str(exc)
        severidade: 'critical' | 'error' | 'warning' | 'info'
        modulo:     Nome do arquivo/função pra rastreio (ex: 'aba_pos_disparar.py::_executar_disparo')
        exc:        Exception object (extrai stack_trace + tipo_erro automaticamente)
        contexto:   Dict qualquer com dados úteis (será gravado como JSONB)
        telefone_cliente: Se erro relacionado a cliente específico
        unidade:    'Mogi das Cruzes' | 'Suzano'
        silencioso: Se True (default), qualquer falha ao gravar retorna None sem
                    interromper fluxo. Se False, re-lança exception.

    Returns:
        id do erro criado, ou None se falhou.
    """
    try:
        # Extrai info da exception se fornecida
        stack_trace_str = None
        tipo_erro_str = None
        if exc is not None:
            stack_trace_str = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            tipo_erro_str = type(exc).__name__
            if mensagem is None:
                mensagem = str(exc) or tipo_erro_str

        if mensagem is None:
            mensagem = "(sem mensagem)"

        # Trunca campos gigantes pra proteger DB
        mensagem = str(mensagem)[:2000]
        if stack_trace_str:
            stack_trace_str = stack_trace_str[:8000]

        sb = _get_sb_erros()
        r = sb.rpc("registrar_erro", {
            "p_robo":             robo,
            "p_origem":           origem,
            "p_mensagem":         mensagem,
            "p_severidade":       severidade,
            "p_modulo":           modulo,
            "p_stack_trace":      stack_trace_str,
            "p_tipo_erro":        tipo_erro_str,
            "p_contexto":         contexto,
            "p_telefone_cliente": telefone_cliente,
            "p_unidade":          unidade,
        }).execute()

        return r.data if r.data else None

    except Exception as e:
        if not silencioso:
            raise
        # Fallback: se nem conseguimos gravar o erro, printa no console
        try:
            print(f"[utils_erros] Falha ao gravar erro no Supabase: {e}")
        except Exception:
            pass
        return None


# ============================================================================
# DECORATOR — captura erros de função inteira
# ============================================================================
def capturar_erros(
    robo: str,
    origem: str = "dashboard",
    severidade: str = "error",
    modulo: Optional[str] = None,
    re_raise: bool = True,
):
    """
    Decorator pra registrar erros de uma função inteira automaticamente.

    @capturar_erros(robo="pos_atendimento", origem="dashboard")
    def minha_funcao(x):
        return 1/x  # se der ZeroDivisionError, registra e (se re_raise=True) re-lança

    Args:
        re_raise: Se True (default), re-lança a exception depois de registrar.
                  Se False, retorna None em caso de erro (silencia).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                mod = modulo or f"{func.__module__}::{func.__name__}"
                registrar_erro(
                    robo=robo,
                    origem=origem,
                    modulo=mod,
                    exc=e,
                    severidade=severidade,
                )
                if re_raise:
                    raise
                return None
        return wrapper
    return decorator


# ============================================================================
# CONTEXT MANAGER — captura erros de bloco de código
# ============================================================================
@contextmanager
def erro_scope(
    robo: str,
    origem: str = "dashboard",
    severidade: str = "error",
    modulo: Optional[str] = None,
    contexto: Optional[Dict[str, Any]] = None,
    telefone_cliente: Optional[str] = None,
    unidade: Optional[str] = None,
    re_raise: bool = True,
):
    """
    Context manager pra capturar erros de bloco de código.

    with erro_scope(robo="pos_atendimento", origem="dashboard",
                    contexto={"batch_size": 25}):
        # se qualquer coisa aqui der erro, registra
        _fazer_algo_perigoso()

    Args:
        re_raise: Se True (default), re-lança depois de registrar.
    """
    try:
        yield
    except Exception as e:
        registrar_erro(
            robo=robo,
            origem=origem,
            modulo=modulo,
            severidade=severidade,
            exc=e,
            contexto=contexto,
            telefone_cliente=telefone_cliente,
            unidade=unidade,
        )
        if re_raise:
            raise
