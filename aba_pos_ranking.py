"""
==============================================================================
ROBÔ PÓS-ATENDIMENTO — Aba "🏆 Ranking Profissionais"
==============================================================================
v1.0 (13/07/2026)

Agrupa clientes por profissional que fez o atendimento e mostra distribuição
de avaliações (satisfeito, satisfeito + cupom, problema atendimento, resultado
ruim, sem resposta).

Categorização dos status:
    😊 Satisfeito sem cupom     : tudo_otimo_cupom_depois, tudo_otimo_pendente
    🎁 Satisfeito + cupom       : tudo_otimo_cupom_agora, cupom_agora_direto
    🚨 Problema atendimento     : problema_atendimento
    😞 Resultado ruim           : resultado_ruim
    🤐 Sem resposta             : template_sem_resposta, expirado_24h,
                                  sem_resposta_24h
    ⚙️  Operacional (ignorados)  : aguardando_disparo, template_enviado,
                                  substituido_por_novo_disparo, falha_envio,
                                  duplicata_ignorada, redirecionado_coordenadora

Taxa de satisfação =
    (satisfeito_sem_cupom + satisfeito_com_cupom) / total_com_resposta
    onde total_com_resposta = satisfeitos + problemas + resultado_ruim
    (sem_resposta NÃO entra no denominador — não penaliza quem tem
    clientes que ignoraram o template)

Filtros:
    - Data da sessão (De / Até)
    - Unidade (Todas / Mogi das Cruzes / Suzano)
    - Mínimo de atendimentos (default 3, evita ruído estatístico)

Ordenação (radio button):
    - Taxa de satisfação
    - Volume de atendimentos
    - Problemas primeiro (quem precisa de atenção)
==============================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta, date
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# CONEXÃO SUPABASE
# ============================================================================
@st.cache_resource
def _get_sb() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# ============================================================================
# CATEGORIZAÇÃO DE STATUS
# ============================================================================
STATUS_SATISFEITO_SEM_CUPOM = {"tudo_otimo_cupom_depois", "tudo_otimo_pendente"}
STATUS_SATISFEITO_COM_CUPOM = {"tudo_otimo_cupom_agora", "cupom_agora_direto"}
STATUS_PROBLEMA_ATENDIMENTO = {"problema_atendimento"}
STATUS_RESULTADO_RUIM       = {"resultado_ruim"}
STATUS_SEM_RESPOSTA         = {"template_sem_resposta", "expirado_24h", "sem_resposta_24h"}
STATUS_OPERACIONAL          = {
    "aguardando_disparo",
    "template_enviado",
    "substituido_por_novo_disparo",
    "falha_envio",
    "duplicata_ignorada",
    "redirecionado_coordenadora",
}


def _categorizar(status: str) -> str:
    if status in STATUS_SATISFEITO_SEM_CUPOM: return "satisfeito_sem_cupom"
    if status in STATUS_SATISFEITO_COM_CUPOM: return "satisfeito_com_cupom"
    if status in STATUS_PROBLEMA_ATENDIMENTO: return "problema_atendimento"
    if status in STATUS_RESULTADO_RUIM:       return "resultado_ruim"
    if status in STATUS_SEM_RESPOSTA:         return "sem_resposta"
    return "operacional"


# ============================================================================
# FETCH SUPABASE (filtrado por data + unidade)
# ============================================================================
@st.cache_data(ttl=60, show_spinner=False)
def _fetch_ranking_data(data_de: date, data_ate: date, unidade: str) -> pd.DataFrame:
    """
    Puxa profissional_completo, unidade, status, data_sessao já filtrado no
    Supabase. Retorna DataFrame com todas as linhas do período/unidade.
    """
    sb = _get_sb()
    q = (
        sb.table("pos_atendimento_clientes")
          .select("profissional_completo, unidade, status, data_sessao")
          .gte("data_sessao", data_de.isoformat())
          .lte("data_sessao", data_ate.isoformat())
    )
    if unidade != "Todas":
        q = q.eq("unidade", unidade)

    # Pagina em blocos de 1000 (limite default da PostgREST) até esgotar
    todos = []
    offset = 0
    PAGE = 1000
    while True:
        r = q.range(offset, offset + PAGE - 1).execute()
        batch = r.data or []
        todos.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
        if offset > 50_000:  # sanity — nunca deveria chegar aqui
            break

    return pd.DataFrame(todos)


# ============================================================================
# RENDER PRINCIPAL
# ============================================================================
def render_aba_pos_ranking():
    st.markdown("## 🏆 Ranking de profissionais")
    st.caption(
        "Distribuição de avaliações agrupada por profissional que fez o atendimento. "
        "Fonte: coluna `profissional_completo` da planilha do UNO. "
        "Clientes em status operacional (aguardando disparo, template enviado, etc) "
        "não entram no cálculo — são contados apenas os que já deram resposta ou "
        "expiraram sem responder."
    )

    # ── Filtros ──
    st.markdown("### 🔎 Filtros")

    # Default: últimos 30 dias
    hoje = datetime.now(TZ_SP).date()
    trinta_dias_atras = hoje - timedelta(days=30)

    if "pos_rank_data_de" not in st.session_state:
        st.session_state.pos_rank_data_de = trinta_dias_atras
    if "pos_rank_data_ate" not in st.session_state:
        st.session_state.pos_rank_data_ate = hoje

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        data_de = st.date_input(
            "De:",
            value=st.session_state.pos_rank_data_de,
            key="pos_rank_dpicker_de",
            format="DD/MM/YYYY",
        )
        st.session_state.pos_rank_data_de = data_de
    with col_d2:
        data_ate = st.date_input(
            "Até:",
            value=st.session_state.pos_rank_data_ate,
            key="pos_rank_dpicker_ate",
            format="DD/MM/YYYY",
        )
        st.session_state.pos_rank_data_ate = data_ate

    if data_de and data_ate and data_de > data_ate:
        st.warning("⚠️ Data inicial é depois da final. Inverta as datas.")
        return

    col_f1, col_f2, col_f3 = st.columns([2, 2, 2])
    with col_f1:
        unidade = st.selectbox(
            "Unidade",
            ["Todas", "Mogi das Cruzes", "Suzano"],
            key="pos_rank_unidade",
        )
    with col_f2:
        min_atend = st.number_input(
            "Mínimo de atendimentos",
            min_value=1, max_value=100, value=3, step=1,
            key="pos_rank_min_atend",
            help="Profissionais com menos que isso não aparecem no ranking. "
                 "Evita ruído estatístico (ex.: 1 atendimento com 100% de problema).",
        )
    with col_f3:
        ordenar_por = st.selectbox(
            "Ordenar por",
            ["Taxa de satisfação (maior primeiro)",
             "Volume de atendimentos (maior primeiro)",
             "Problemas primeiro (quem precisa de atenção)"],
            key="pos_rank_ordenar",
        )

    # ── Fetch ──
    with st.spinner("Carregando dados..."):
        df = _fetch_ranking_data(data_de, data_ate, unidade)

    if df.empty:
        st.info("Nenhum atendimento no período/unidade selecionado.")
        return

    # ── Preparação ──
    df["categoria"] = df["status"].apply(_categorizar)

    # Descarta operacionais
    df = df[df["categoria"] != "operacional"].copy()

    # Descarta profissional vazio/nulo
    df = df[df["profissional_completo"].notna()].copy()
    df["profissional_completo"] = df["profissional_completo"].astype(str).str.strip()
    df = df[df["profissional_completo"] != ""]

    if df.empty:
        st.info(
            "Nenhum atendimento com resposta no período. "
            "Todos os clientes estão em status operacional (aguardando disparo, "
            "template enviado, etc). Espere 24h ou amplie o período de busca."
        )
        return

    # ── Agrupamento ──
    grp = (
        df.groupby(["profissional_completo", "unidade", "categoria"])
          .size()
          .unstack(fill_value=0)
          .reset_index()
    )

    # Garante todas as colunas de categoria (mesmo que 0)
    for cat in ["satisfeito_sem_cupom", "satisfeito_com_cupom",
                "problema_atendimento", "resultado_ruim", "sem_resposta"]:
        if cat not in grp.columns:
            grp[cat] = 0

    # Métricas derivadas
    grp["satisfeitos_total"] = grp["satisfeito_sem_cupom"] + grp["satisfeito_com_cupom"]
    grp["problemas_total"]   = grp["problema_atendimento"] + grp["resultado_ruim"]
    grp["com_resposta"]      = grp["satisfeitos_total"] + grp["problemas_total"]
    grp["total_geral"]       = grp["com_resposta"] + grp["sem_resposta"]

    # Taxa de satisfação (só quem deu resposta)
    grp["taxa_satisfacao"] = grp.apply(
        lambda r: (r["satisfeitos_total"] / r["com_resposta"] * 100) if r["com_resposta"] > 0 else 0.0,
        axis=1
    ).round(1)

    # ── Aplica filtro de mínimo de atendimentos ──
    grp_filtrado = grp[grp["total_geral"] >= min_atend].copy()
    filtrados_fora = len(grp) - len(grp_filtrado)

    if grp_filtrado.empty:
        st.warning(
            f"⚠️ Nenhuma profissional tem {min_atend}+ atendimentos no período. "
            f"Diminua o mínimo ou amplie o período."
        )
        st.caption(f"({len(grp)} profissional(is) encontrado(s) no total, mas todas abaixo do mínimo)")
        return

    # ── Ordenação ──
    if ordenar_por.startswith("Taxa"):
        # Empate → maior volume desempata
        grp_filtrado = grp_filtrado.sort_values(
            ["taxa_satisfacao", "com_resposta"], ascending=[False, False]
        )
    elif ordenar_por.startswith("Volume"):
        grp_filtrado = grp_filtrado.sort_values("total_geral", ascending=False)
    else:  # Problemas primeiro
        # Ordena por total de problemas desc, depois taxa satisfação asc
        grp_filtrado = grp_filtrado.sort_values(
            ["problemas_total", "taxa_satisfacao"], ascending=[False, True]
        )

    # ── Métricas globais no topo ──
    st.markdown("### 📊 Resumo do período")
    total_prof = len(grp_filtrado)
    total_atend_geral = int(grp_filtrado["total_geral"].sum())
    total_com_resp    = int(grp_filtrado["com_resposta"].sum())
    total_satisfeitos = int(grp_filtrado["satisfeitos_total"].sum())
    total_problemas   = int(grp_filtrado["problemas_total"].sum())
    total_cupom       = int(grp_filtrado["satisfeito_com_cupom"].sum())
    taxa_global = (total_satisfeitos / total_com_resp * 100) if total_com_resp > 0 else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("👩 Profissionais", total_prof)
    m2.metric("👥 Atendimentos", total_atend_geral)
    m3.metric("😊 Satisfação global", f"{taxa_global:.1f}%")
    m4.metric("🚨 Problemas", total_problemas)
    m5.metric("🎁 Pediram cupom", total_cupom)

    if filtrados_fora > 0:
        st.caption(
            f"ℹ️ {filtrados_fora} profissional(is) ocultada(s) por ter(em) menos de "
            f"{min_atend} atendimento(s) no período."
        )

    # ── Tabela do ranking ──
    st.markdown("### 🏆 Ranking")

    # Prepara df de display renomeando colunas
    display = grp_filtrado.rename(columns={
        "profissional_completo":  "Profissional",
        "unidade":                "Unidade",
        "total_geral":            "Total",
        "com_resposta":           "C/ resposta",
        "satisfeito_sem_cupom":   "😊 Satisfeito",
        "satisfeito_com_cupom":   "🎁 Satisfeito + cupom",
        "problema_atendimento":   "🚨 Prob. atendimento",
        "resultado_ruim":         "😞 Resultado ruim",
        "sem_resposta":           "🤐 Sem resposta",
        "taxa_satisfacao":        "Taxa satisfação",
    })

    colunas_ordenadas = [
        "Profissional",
        "Unidade",
        "Total",
        "C/ resposta",
        "😊 Satisfeito",
        "🎁 Satisfeito + cupom",
        "🚨 Prob. atendimento",
        "😞 Resultado ruim",
        "🤐 Sem resposta",
        "Taxa satisfação",
    ]

    st.dataframe(
        display[colunas_ordenadas],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Profissional": st.column_config.TextColumn(width="medium"),
            "Unidade":      st.column_config.TextColumn(width="small"),
            "Total":        st.column_config.NumberColumn(
                width="small",
                help="Total geral (com resposta + sem resposta)",
            ),
            "C/ resposta":  st.column_config.NumberColumn(
                width="small",
                help="Base do cálculo da taxa de satisfação",
            ),
            "😊 Satisfeito": st.column_config.NumberColumn(width="small"),
            "🎁 Satisfeito + cupom": st.column_config.NumberColumn(
                width="small",
                help="Clientes que pediram o cupom agora (mais engajados comercialmente)",
            ),
            "🚨 Prob. atendimento": st.column_config.NumberColumn(
                width="small",
                help="Problema com a profissional (atendimento)",
            ),
            "😞 Resultado ruim":    st.column_config.NumberColumn(
                width="small",
                help="Cliente insatisfeito com o resultado do serviço",
            ),
            "🤐 Sem resposta":      st.column_config.NumberColumn(width="small"),
            "Taxa satisfação":      st.column_config.ProgressColumn(
                width="medium",
                format="%.1f%%",
                min_value=0.0,
                max_value=100.0,
                help="Satisfeitos ÷ (Satisfeitos + Problemas + Resultado ruim). "
                     "Sem resposta NÃO entra no cálculo.",
            ),
        },
    )

    st.caption(
        f"Ranking baseado em {total_atend_geral} atendimento(s) de "
        f"{total_prof} profissional(is), período de {data_de.strftime('%d/%m/%Y')} "
        f"a {data_ate.strftime('%d/%m/%Y')}."
    )
