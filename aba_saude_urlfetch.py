# -*- coding: utf-8 -*-
"""
Card de Cota UrlFetch - 5 robos
v1.3 (11/08/2026)

v1.3: FIX CRITICO — as_completed(timeout=N) levanta TimeoutError que
      quebra a pagina inteira quando qualquer future demora mais que
      o timeout total. Bug latente desde v1.0 (era mascarado porque
      timeout individual 5s < timeout total 6s, entao quase nunca
      chegava a estourar o total). Com v1.2 (individual 15s, total 18s)
      ficou vulneravel — se algum robo em cold start passa de 15s,
      o loop as_completed estoura TimeoutError e a UI quebra.
      Correcao: envolver o for as_completed em try/except
      FuturesTimeoutError, marcando as futures pendentes como erro
      individual e continuando com os resultados parciais.
      Zero impacto na UX quando tudo funciona.
v1.2: FIX timeout curto que causava erro visual rotativo nos 5 robos:
      - Timeout individual: 5s -> 15s (Apps Script cold start / fila
        de execucao serializada pode passar de 5s facil, causando
        HTTPSConnectionPool Read timed out no card).
      - Timeout total as_completed: 6s -> 18s (coerente com o novo
        individual + margem de 3s).
      - Zero mudanca nos robos. Fix 100% no dashboard.
v1.1: FIX cirurgico pos-migracao Google Workspace Business Starter:
      - LIMITE_DIA_DEFAULT: 20000 -> 100000 (quota Workspace, 5x mais)
      - Total sistema: agora usa a quota compartilhada correta (100k
        por conta Google, nao a soma dos limites de cada script).
        Antes: total_limite = sum(cada_robo["limite_dia"]) -> falso 500k
        Agora: total_limite = LIMITE_DIA_DEFAULT (100k, compartilhado)
      - Cada barra: sempre usa LIMITE_DIA_DEFAULT como denominador,
        ignorando o "limite_dia" retornado pelo robo (que pode estar
        desatualizado com 20000 no ScriptProperty).
      - PCT recalculado localmente em vez de confiar no que vem do robo.
      - Legenda atualizada: "Workspace Business Starter: 100k/dia,
        reseta 24h apos 1o fetch" (Google nao reseta a meia-noite).
v1.0: Versao inicial (07/08/2026) - assumia Gmail free 20k por script.

Consulta 5 endpoints Apps Script (Agenda, Bia, IeG, Pos, MKT) e mostra
consumo diario de UrlFetch. Cotas do Google Workspace: 100.000/dia
compartilhado entre todos os scripts da mesma conta Google.

Config obrigatoria em .streamlit/secrets.toml:

    [urlfetch]
    agenda_url = "https://script.google.com/macros/s/.../exec"
    agenda_token = "..."
    bia_url = "https://script.google.com/macros/s/.../exec"
    bia_token = "..."
    ieg_url = "https://script.google.com/macros/s/.../exec"
    ieg_token = "..."
    pos_url = "https://script.google.com/macros/s/.../exec"
    pos_token = "..."
    mkt_url = "https://script.google.com/macros/s/.../exec"
    mkt_token = "..."

Estrategia defensiva pra Streamlit 1.35:
    - Sem type hints modernos
    - Sem @st.cache_resource
    - Sem st.container(border=True)
    - Try/except em cada acesso a secrets
    - Concatenacao simples de strings
    - Requests paralelas via ThreadPoolExecutor
"""

import streamlit as st
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError


# ============================================================================
# CONFIG DOS ROBOS
# ============================================================================
# 4 primeiros usam ?endpoint=uso, MKT usa ?action=uso
ROBOS_CONFIG = [
    {"nome": "Agenda",     "secret_url": "agenda_url", "secret_token": "agenda_token", "param": "endpoint"},
    {"nome": "Filtro Bia", "secret_url": "bia_url",    "secret_token": "bia_token",    "param": "endpoint"},
    {"nome": "IeG",        "secret_url": "ieg_url",    "secret_token": "ieg_token",    "param": "endpoint"},
    {"nome": "Pos",        "secret_url": "pos_url",    "secret_token": "pos_token",    "param": "endpoint"},
    {"nome": "MKT",        "secret_url": "mkt_url",    "secret_token": "mkt_token",    "param": "action"},
]

LIMITE_DIA_DEFAULT = 100000  # v1.1: Workspace Business Starter (era 20000 no Gmail free)


# ============================================================================
# HELPERS
# ============================================================================
def _get_secret(chave):
    """Retorna valor do secret ou None se ausente."""
    try:
        return st.secrets["urlfetch"][chave]
    except Exception:
        try:
            return st.secrets[chave]
        except Exception:
            return None


def _fetch_uso_robo(config):
    """
    Chama endpoint 'uso' do robo e retorna dict com resultado.
    Timeout 15s (v1.2). Retorna dict com erro em caso de falha.
    """
    nome = config["nome"]
    url = _get_secret(config["secret_url"])
    token = _get_secret(config["secret_token"])
    param = config["param"]

    if not url or not token:
        return {
            "nome": nome,
            "ok": False,
            "erro": "url/token nao configurado em secrets.toml",
            "urlfetch_hoje": 0,
            "limite_dia": LIMITE_DIA_DEFAULT,
            "pct": 0,
        }

    try:
        params = {param: "uso", "token": token}
        r = requests.get(url, params=params, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            return {
                "nome": nome,
                "ok": False,
                "erro": "HTTP " + str(r.status_code),
                "urlfetch_hoje": 0,
                "limite_dia": LIMITE_DIA_DEFAULT,
                "pct": 0,
            }
        data = r.json()
        # Tolerante: pode vir dentro de outro campo dependendo do robo
        if isinstance(data, dict) and "urlfetch_hoje" in data:
            payload = data
        elif isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            payload = data["data"]
        else:
            payload = data if isinstance(data, dict) else {}

        return {
            "nome": nome,
            "ok": True,
            "erro": None,
            "urlfetch_hoje": int(payload.get("urlfetch_hoje", 0)),
            "limite_dia": int(payload.get("limite_dia", LIMITE_DIA_DEFAULT)),
            "pct": int(payload.get("pct", 0)),
            "data": payload.get("data", ""),
        }
    except Exception as e:
        return {
            "nome": nome,
            "ok": False,
            "erro": str(e)[:100],
            "urlfetch_hoje": 0,
            "limite_dia": LIMITE_DIA_DEFAULT,
            "pct": 0,
        }


def _fetch_all_paralelo():
    """Chama os 5 endpoints em paralelo com timeout total 18s (v1.2).
    v1.3: try/except FuturesTimeoutError pra nao quebrar a pagina se
    o timeout total estourar — futures pendentes viram erro individual."""
    resultados = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        # v1.3: mapear future -> cfg pra saber qual robo em caso de timeout do loop
        future_to_cfg = {ex.submit(_fetch_uso_robo, cfg): cfg for cfg in ROBOS_CONFIG}
        futures = list(future_to_cfg.keys())
        try:
            for f in as_completed(futures, timeout=18):
                try:
                    resultados.append(f.result())
                except Exception as e:
                    cfg_erro = future_to_cfg.get(f, {})
                    resultados.append({
                        "nome": cfg_erro.get("nome", "?"),
                        "ok": False,
                        "erro": str(e)[:100],
                        "urlfetch_hoje": 0,
                        "limite_dia": LIMITE_DIA_DEFAULT,
                        "pct": 0,
                    })
        except FuturesTimeoutError:
            # v1.3: timeout total estourou. Marca as futures que ainda nao completaram
            # como erro individual e mantem as que ja voltaram.
            nomes_ja_ok = {r["nome"] for r in resultados}
            for fut, cfg in future_to_cfg.items():
                if cfg["nome"] in nomes_ja_ok:
                    continue
                if fut.done():
                    try:
                        resultados.append(fut.result())
                    except Exception as e:
                        resultados.append({
                            "nome": cfg["nome"], "ok": False,
                            "erro": str(e)[:100],
                            "urlfetch_hoje": 0, "limite_dia": LIMITE_DIA_DEFAULT, "pct": 0,
                        })
                else:
                    resultados.append({
                        "nome": cfg["nome"], "ok": False,
                        "erro": "timeout global (>18s) - Apps Script cold start?",
                        "urlfetch_hoje": 0, "limite_dia": LIMITE_DIA_DEFAULT, "pct": 0,
                    })
    # Preservar ordem original
    ordem = {c["nome"]: i for i, c in enumerate(ROBOS_CONFIG)}
    resultados.sort(key=lambda r: ordem.get(r["nome"], 99))
    return resultados


def _cor_semaforo(pct):
    """Verde <60, amarelo 60-85, vermelho >85."""
    if pct >= 85:
        return "vermelho"
    if pct >= 60:
        return "amarelo"
    return "verde"


def _emoji_semaforo(pct):
    if pct >= 85:
        return "🔴"
    if pct >= 60:
        return "🟡"
    return "🟢"


# ============================================================================
# UI
# ============================================================================
def render_cota_urlfetch():
    """Renderiza card completo de cota UrlFetch dos 5 robos."""
    st.markdown("### ⚡ Cota UrlFetch (hoje)")

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        atualizar = st.button("🔄 Atualizar", key="urlfetch_refresh")

    # Sempre busca em cada render pra ter dado fresco (nao usa cache)
    with st.spinner("Consultando os 5 robos..."):
        resultados = _fetch_all_paralelo()

    # v1.1: Total sistema usa a quota compartilhada da conta Google (100k),
    # nao a soma dos limites individuais (que dava falso 500k).
    # Explicacao: quota UrlFetch e POR USER Google, nao POR SCRIPT.
    # Como os 5 scripts estao sob a mesma conta carlos@franquiasmaislaser.com.br,
    # todos compartilham o mesmo teto de 100000 fetches/dia.
    total_hoje = sum(r["urlfetch_hoje"] for r in resultados if r["ok"])
    total_limite = LIMITE_DIA_DEFAULT
    pct_total = int((total_hoje / total_limite) * 100) if total_limite else 0
    total_emoji = _emoji_semaforo(pct_total)

    with col_info:
        st.markdown(
            "**Total sistema:** " + total_emoji + " " +
            str(total_hoje) + " / " + str(total_limite) +
            " (" + str(pct_total) + "%)"
        )

    st.divider()

    # 5 barras (uma por robo)
    for r in resultados:
        nome = r["nome"]
        if not r["ok"]:
            st.error("**" + nome + "** — erro: " + str(r.get("erro") or "desconhecido"))
            continue

        # v1.1: usa quota compartilhada (100k) como denominador em vez do
        # r["limite_dia"] que pode estar desatualizado (20k) no ScriptProperty.
        # PCT recalculado localmente pra evitar inconsistencia com o total.
        hoje = r["urlfetch_hoje"]
        limite = LIMITE_DIA_DEFAULT
        pct = int((hoje / limite) * 100) if limite else 0
        emoji = _emoji_semaforo(pct)

        col_nome, col_bar, col_num = st.columns([1, 3, 1])
        with col_nome:
            st.markdown("**" + emoji + " " + nome + "**")
        with col_bar:
            # Progress bar (0.0 a 1.0)
            valor = pct / 100.0
            if valor > 1.0:
                valor = 1.0
            if valor < 0:
                valor = 0.0
            st.progress(valor)
        with col_num:
            st.markdown(str(hoje) + " / " + str(limite))

    # v1.1: Aviso quando algum >= 85% recalculado com limite compartilhado
    criticos = []
    for r in resultados:
        if not r["ok"]:
            continue
        pct_local = int((r["urlfetch_hoje"] / LIMITE_DIA_DEFAULT) * 100) if LIMITE_DIA_DEFAULT else 0
        if pct_local >= 85:
            criticos.append(r)
    if criticos:
        nomes = ", ".join(c["nome"] for c in criticos)
        st.error(
            "🚨 **Atenção:** " + nomes +
            " passaram de 85% da cota diaria. Risco de estouro."
        )

    st.caption(
        "Cota Google Workspace Business Starter: 100.000 fetches/dia "
        "compartilhado entre os 5 scripts (mesma conta Google). "
        "Reseta 24h apos 1o fetch do ciclo anterior "
        "(nao a meia-noite). "
        "Verde <60% | Amarelo 60-85% | Vermelho >85%."
    )
