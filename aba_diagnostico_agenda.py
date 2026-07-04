"""
==============================================================================
ABA DIAGNÓSTICO AGENDA — Painel completo de saúde do Robô Agenda
==============================================================================
v1.0 (04/07/2026): Fase 4.7 da migração Supabase.

FONTES:
    1. Supabase RPC agenda_diagnostico() — dados de contexto/log/disparos
       críticos, atenção, saúde, volumes 30d, heatmap, recorrentes, Mogi vs
       Suzano.
    2. Apps Script GET ?endpoint=diagnostico_agenda&token=X — dados que só
       o Apps Script vê: PropertiesService, triggers, abas Sheets.

ESTRUTURA DA UI:
    Header  — status geral + timestamp + botões (recarregar / download JSON)
    🔴 CRÍTICOS      — 5 checks expansíveis com detalhes
    🟡 ATENÇÃO       — 4 checks expansíveis com detalhes
    🟢 SAÚDE          — cards + pie por status + Mogi vs Suzano
    ⚙️ INFRAESTRUTURA — triggers, props, abas Sheets, dual-write
    📊 EXTRAS         — volumes 30d, heatmap horas 7d, recorrentes

CACHE:
    30s TTL — mesmo padrão das outras abas.
==============================================================================
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# HELPERS DE CONEXÃO
# ============================================================================

@st.cache_resource
def _get_sb_client() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_diagnostico_supabase() -> dict:
    """Chama a RPC agenda_diagnostico() no Supabase."""
    try:
        sb = _get_sb_client()
        r = sb.rpc("agenda_diagnostico", {}).execute()
        if isinstance(r.data, dict):
            return r.data
        return {"_erro": "RPC retornou formato inesperado"}
    except Exception as e:
        return {"_erro": f"Erro ao chamar agenda_diagnostico(): {e}"}


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_diagnostico_apps_script() -> dict:
    """Chama ?endpoint=diagnostico_agenda no Apps Script."""
    try:
        url = st.secrets["APPS_SCRIPT_URL"]
        token = st.secrets["APPS_SCRIPT_TOKEN"]
    except Exception:
        return {"_erro": "Config ausente: APPS_SCRIPT_URL/TOKEN"}

    try:
        resp = requests.get(
            url,
            params={"endpoint": "diagnostico_agenda", "token": token},
            timeout=30,
            allow_redirects=True
        )
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code}"}
        data = resp.json()
        if isinstance(data, dict) and data.get("erro"):
            return {"_erro": f"Apps Script: {data['erro']}"}
        return data
    except requests.exceptions.Timeout:
        return {"_erro": "Apps Script demorou demais (>30s)"}
    except Exception as e:
        return {"_erro": f"Erro de rede: {e}"}


# ============================================================================
# HELPERS DE UI
# ============================================================================

def _render_card(icon, value, label, cor="#5BC0BE", sub=None):
    sub_html = f'<div style="font-size:11px;color:#9CA3AF;margin-top:2px;">{sub}</div>' if sub else ''
    return (
        f'<div style="background:white;border-radius:12px;padding:16px;border:1px solid #E5E7EB;box-shadow:0 1px 2px rgba(0,0,0,0.03);">'
        f'<div style="display:flex;align-items:center;gap:12px;">'
        f'<div style="width:40px;height:40px;border-radius:10px;background:{cor}1A;color:{cor};display:flex;align-items:center;justify-content:center;font-size:22px;">{icon}</div>'
        f'<div>'
        f'<div style="font-size:24px;font-weight:700;color:#111827;">{value}</div>'
        f'<div style="font-size:12px;color:#6B7280;text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
        f'{sub_html}'
        f'</div></div></div>'
    )


def _render_status_banner(criticos, atencao):
    """Banner grande no topo dizendo o estado geral do sistema."""
    if criticos > 0:
        cor_bg = "#FEE2E2"; cor_txt = "#B91C1C"; icon = "🔴"
        titulo = f"{criticos} problema(s) crítico(s) detectado(s)"
        subtitulo = f"Também há {atencao} ponto(s) de atenção." if atencao > 0 else "Verifique detalhes abaixo."
    elif atencao > 0:
        cor_bg = "#FEF3C7"; cor_txt = "#92400E"; icon = "🟡"
        titulo = f"{atencao} ponto(s) de atenção"
        subtitulo = "Nada crítico, mas vale investigar."
    else:
        cor_bg = "#D1FAE5"; cor_txt = "#065F46"; icon = "🟢"
        titulo = "Sistema saudável — nenhum problema detectado"
        subtitulo = "Robô rodando limpo."

    st.markdown(f"""
    <div style="background: {cor_bg}; border-left: 4px solid {cor_txt};
                padding: 16px 20px; border-radius: 8px; margin-bottom: 20px;">
        <div style="display: flex; align-items: center; gap: 14px;">
            <div style="font-size: 36px;">{icon}</div>
            <div>
                <div style="font-weight: 700; font-size: 16px; color: {cor_txt};">{titulo}</div>
                <div style="font-size: 13px; color: {cor_txt}; opacity: 0.9; margin-top: 3px;">{subtitulo}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _detalhe_expansivel(titulo_icone, count, chave_expander, tabela_render_fn):
    """Wrapper genérico pra seção expansível de check."""
    label = f"{titulo_icone}  ·  **{count}** caso(s)"
    with st.expander(label, expanded=(count > 0 and count <= 5)):
        if count == 0:
            st.success("✅ Nenhum problema neste check.")
        else:
            tabela_render_fn()


def _fmt_dt(iso_str):
    """Formata ISO string pra dd/mm HH:MM local."""
    if not iso_str:
        return "—"
    try:
        dt = pd.to_datetime(iso_str, utc=True).tz_convert(TZ_SP)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return str(iso_str)[:16].replace("T", " ")


# ============================================================================
# ABA PRINCIPAL
# ============================================================================

def render_aba_diagnostico_agenda():
    st.markdown("## 🔧 Diagnóstico Robô Agenda")
    st.caption("Painel completo de saúde do sistema — checks automáticos em contexto, log, disparos, PropertiesService, triggers e infra.")

    # ── Carrega dados das 2 fontes ──
    with st.spinner("🔎 Executando checks..."):
        diag_sb = _fetch_diagnostico_supabase()
        diag_as = _fetch_diagnostico_apps_script()

    if diag_sb.get("_erro"):
        st.error(f"❌ **Falha ao consultar Supabase:** {diag_sb['_erro']}")
        return

    resumo = diag_sb.get("resumo", {})
    criticos_count = resumo.get("criticos", 0)
    atencao_count = resumo.get("atencao", 0)

    # ── HEADER: banner + toolbar ──
    _render_status_banner(criticos_count, atencao_count)

    col_ts, col_reload, col_dl = st.columns([3, 1, 1])
    with col_ts:
        gerado_supabase = _fmt_dt(diag_sb.get("gerado_em"))
        gerado_as = _fmt_dt(diag_as.get("gerado_em")) if not diag_as.get("_erro") else "erro"
        st.caption(f"⏱️ Supabase: **{gerado_supabase}** · Apps Script: **{gerado_as}** · Cache 30s")
    with col_reload:
        if st.button("🔄 Recarregar", use_container_width=True, key="diag_reload"):
            st.cache_data.clear()
            st.rerun()
    with col_dl:
        snapshot_completo = {
            "supabase": diag_sb,
            "apps_script": diag_as,
            "gerado_em_dashboard": datetime.now(TZ_SP).isoformat()
        }
        st.download_button(
            "📥 Snapshot",
            data=json.dumps(snapshot_completo, indent=2, default=str),
            file_name=f"diagnostico_agenda_{datetime.now(TZ_SP).strftime('%Y%m%d-%H%M')}.json",
            mime="application/json",
            use_container_width=True,
            key="diag_download",
        )

    st.divider()

    # =========================================================================
    # SEÇÃO 1: 🔴 CRÍTICOS
    # =========================================================================
    st.markdown("### 🔴 Críticos")
    st.caption("Problemas que precisam de ação — clientes com estado inconsistente ou fluxos quebrados.")

    criticos = diag_sb.get("criticos", {})

    def _render_aguardando_travado():
        det = criticos.get("aguardando_travado", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df = df.rename(columns={"telefone":"Telefone","nome":"Cliente","unidade":"Unidade","horas_travado":"Horas travado"})
        st.info("Clientes com status='aguardando' há +24h mas sem `disparo_ts` recente. Deveriam ter virado `cancelado_sem_resposta`.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    def _render_indicacao_travada():
        det = criticos.get("indicacao_travada", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df = df.rename(columns={"telefone":"Telefone","nome":"Cliente","unidade":"Unidade","horas_travado":"Horas travado"})
        st.info("Convites de indicação pendentes há +25h sem `ind_convite_ts` recente. Deveriam ter fechado.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    def _render_recepcao_parado():
        det = criticos.get("recepcao_parado", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df = df.rename(columns={"telefone":"Telefone","nome":"Cliente","unidade":"Unidade","status":"Status","horas_parado":"Horas parado"})
        st.warning("Clientes redirecionados/aguardando recepção há +72h. Precisa de ação manual da recepção (esses estados **não fecham sozinhos por design**).")
        st.dataframe(df, use_container_width=True, hide_index=True)

    def _render_duplicatas():
        det = criticos.get("duplicatas_telefone", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df = df.rename(columns={"telefone":"Telefone","vezes":"Ocorrências"})
        st.error("Mesmo telefone em múltiplas linhas ativas. Rode `detectarDuplicatasNoContexto()` no Apps Script pra investigar.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    def _render_aguardando_sem_disparo():
        det = criticos.get("aguardando_sem_disparo", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df["ultima_atualizacao"] = df["ultima_atualizacao"].apply(_fmt_dt)
        df = df.rename(columns={"telefone":"Telefone","nome":"Cliente","unidade":"Unidade","ultima_atualizacao":"Última alteração"})
        st.warning("Cliente com status='aguardando' mas SEM `disparo_ts` — não vai receber lembrete automático.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    _detalhe_expansivel("⏰ Aguardando travado >24h", criticos.get("aguardando_travado", {}).get("count", 0), "c_aguard", _render_aguardando_travado)
    _detalhe_expansivel("🎁 Indicação travada >25h", criticos.get("indicacao_travada", {}).get("count", 0), "c_indic", _render_indicacao_travada)
    _detalhe_expansivel("🛎️ Recepção parado >72h",  criticos.get("recepcao_parado", {}).get("count", 0), "c_recep", _render_recepcao_parado)
    _detalhe_expansivel("👯 Duplicatas de telefone",  criticos.get("duplicatas_telefone", {}).get("count", 0), "c_dup", _render_duplicatas)
    _detalhe_expansivel("🔍 Aguardando sem disparo_ts", criticos.get("aguardando_sem_disparo", {}).get("count", 0), "c_semd", _render_aguardando_sem_disparo)

    st.divider()

    # =========================================================================
    # SEÇÃO 2: 🟡 ATENÇÃO
    # =========================================================================
    st.markdown("### 🟡 Atenção")
    st.caption("Pontos que não bloqueiam operação mas valem investigação.")

    atencao = diag_sb.get("atencao", {})

    def _render_confirmado_com_disparo():
        det = atencao.get("confirmado_com_disparo", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df = df.rename(columns={"telefone":"Telefone","nome":"Cliente"})
        st.info("Clientes confirmados que ainda têm `disparo_ts` — ciclo não fechou completamente.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    def _render_indicacao_sem_convite():
        det = atencao.get("indicacao_sem_convite", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df = df.rename(columns={"telefone":"Telefone","nome":"Cliente"})
        st.info("Status=indicacao_pendente mas SEM `ind_convite_ts`. Estado inconsistente.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    def _render_erros_meta():
        det = atencao.get("erros_meta_24h", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df["data_hora"] = df["data_hora"].apply(_fmt_dt)
        df = df.rename(columns={"data_hora":"Quando","telefone":"Telefone","nome":"Cliente","observacao":"Erro"})
        st.warning("Falhas de envio ao Meta detectadas nas últimas 24h.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    def _render_disparos_falha():
        det = atencao.get("disparos_com_falha_7d", {}).get("detalhes", [])
        df = pd.DataFrame(det)
        if df.empty: return
        df["criado_em"] = df["criado_em"].apply(_fmt_dt)
        # Detalhes pode vir null (disparos anteriores à v6.14.2 que introduziu a coluna)
        if "erros_envio_detalhes" in df.columns:
            df["erros_envio_detalhes"] = df["erros_envio_detalhes"].fillna("—").replace("", "—")
        df = df.rename(columns={
            "criado_em":"Quando","unidade":"Unidade","total_clientes":"Total",
            "whatsapp_ok":"Enviados","erros_envio":"Erros","erros_envio_detalhes":"Detalhes"
        })
        st.warning("Disparos com pelo menos 1 falha nos últimos 7 dias.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    _detalhe_expansivel("✅ Confirmado com disparo_ts ativo", atencao.get("confirmado_com_disparo", {}).get("count", 0), "a_conf", _render_confirmado_com_disparo)
    _detalhe_expansivel("🎁 Indicação sem ind_convite_ts", atencao.get("indicacao_sem_convite", {}).get("count", 0), "a_indic", _render_indicacao_sem_convite)
    _detalhe_expansivel("❌ Erros Meta últimas 24h", atencao.get("erros_meta_24h", {}).get("count", 0), "a_meta", _render_erros_meta)
    _detalhe_expansivel("📤 Disparos com falha 7d", atencao.get("disparos_com_falha_7d", {}).get("count", 0), "a_disp", _render_disparos_falha)

    st.divider()

    # =========================================================================
    # SEÇÃO 3: 🟢 SAÚDE
    # =========================================================================
    st.markdown("### 🟢 Saúde")

    saude = diag_sb.get("saude", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.markdown(_render_card("👥", saude.get("total_ativos", 0), "Ativos", "#5BC0BE"), unsafe_allow_html=True)
    col2.markdown(_render_card("✅", f"{saude.get('taxa_confirmacao_7d', 0)}%", "Confirmação 7d", "#22c55e"), unsafe_allow_html=True)
    col3.markdown(_render_card("🎁", f"{saude.get('taxa_aceite_indicacao_7d', 0)}%", "Aceite indic. 7d", "#a855f7"), unsafe_allow_html=True)
    col4.markdown(_render_card("💬", saude.get("total_log", 0), "Total no log", "#3b82f6"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**📊 Distribuição por status**")
        por_status = saude.get("por_status", {})
        if por_status:
            df_st = pd.DataFrame([{"Status": k, "Qtd": v} for k, v in por_status.items()]).sort_values("Qtd", ascending=True)
            fig = px.bar(df_st, x="Qtd", y="Status", orientation="h",
                         color_discrete_sequence=["#5BC0BE"], text="Qtd")
            fig.update_traces(textposition="outside")
            fig.update_layout(height=340, margin=dict(t=10, b=10, l=10, r=30),
                              yaxis_title=None, xaxis_title=None)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados.")

    with col_b:
        st.markdown("**⚖️ Mogi vs Suzano (últimos 7 dias)**")
        mvs = diag_sb.get("mogi_vs_suzano", {})
        mogi = mvs.get("mogi", {})
        suz = mvs.get("suzano", {})

        df_comp = pd.DataFrame([
            {"Métrica": "Ativos agora",           "Mogi": mogi.get("ativos", 0),               "Suzano": suz.get("ativos", 0)},
            {"Métrica": "Confirmações 7d",        "Mogi": mogi.get("confirmado_7d", 0),        "Suzano": suz.get("confirmado_7d", 0)},
            {"Métrica": "Cancelamentos 7d",       "Mogi": mogi.get("cancelado_7d", 0),         "Suzano": suz.get("cancelado_7d", 0)},
            {"Métrica": "Indicações aceitas 7d",  "Mogi": mogi.get("indicacoes_aceitas_7d", 0),"Suzano": suz.get("indicacoes_aceitas_7d", 0)},
        ])
        st.dataframe(df_comp, use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col_c1, col_c2, col_c3 = st.columns(3)
    col_c1.metric("Última mensagem no log", _fmt_dt(saude.get("ultima_msg_log")))
    col_c2.metric("Contexto arquivado (histórico)", f"{saude.get('total_contexto_arquivado', 0):,}".replace(",", "."))
    col_c3.metric("Feature flag Supabase", "✅ ATIVO" if saude.get("feature_flag_supabase_ativo") else "❌ DESLIGADO")

    if saude.get("modo_manutencao"):
        st.error("⚠️ **MODO MANUTENÇÃO ATIVO** — robô parado por kill switch. Confirme se é intencional.")

    st.divider()

    # =========================================================================
    # SEÇÃO 4: ⚙️ INFRAESTRUTURA (Apps Script)
    # =========================================================================
    st.markdown("### ⚙️ Infraestrutura")
    st.caption("PropertiesService, triggers e abas do Sheets — visíveis apenas via Apps Script.")

    if diag_as.get("_erro"):
        st.error(f"❌ **Apps Script indisponível:** {diag_as['_erro']}")
        st.info("Sem esses dados, alguns checks de infra ficam limitados. Verifique deployment do endpoint `diagnostico_agenda` no Code.gs.")
    else:
        # Alertas customizados do Apps Script (feature flag off, triggers ausentes, etc)
        alertas_as = diag_as.get("alertas", [])
        if alertas_as:
            for a in alertas_as:
                sev = a.get("severidade", "atencao")
                msg = a.get("msg", "?")
                if sev == "critico":
                    st.error(f"🔴 {msg}")
                else:
                    st.warning(f"🟡 {msg}")

        # Cards principais
        col_i1, col_i2, col_i3, col_i4 = st.columns(4)
        trg = diag_as.get("triggers", {})
        col_i1.markdown(_render_card("⚙️", f"{trg.get('total',0)}/{trg.get('limite_apps_script',30)}", "Triggers ativos", "#5BC0BE"), unsafe_allow_html=True)
        col_i2.markdown(_render_card("📦", diag_as.get("total_props", 0), "Propriedades", "#3b82f6"), unsafe_allow_html=True)
        col_i3.markdown(_render_card("👻", diag_as.get("orfaos", {}).get("total", 0), "Órfãs", "#ef4444" if diag_as.get("orfaos", {}).get("total", 0) > 0 else "#22c55e"), unsafe_allow_html=True)
        col_i4.markdown(_render_card("🕯️", diag_as.get("fantasmas", {}).get("total", 0), "Fantasmas", "#ef4444" if diag_as.get("fantasmas", {}).get("total", 0) > 0 else "#22c55e"), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        col_x, col_y = st.columns(2)

        with col_x:
            st.markdown("**Triggers por função**")
            por_h = trg.get("por_handler", {})
            if por_h:
                df_trg = pd.DataFrame([{"Função": k, "Qtd": v} for k, v in por_h.items()])
                st.dataframe(df_trg, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum trigger ativo.")

        with col_y:
            st.markdown("**Abas na planilha**")
            abas = diag_as.get("abas_sheets", [])
            if abas:
                df_abas = pd.DataFrame(abas).rename(columns={"nome":"Aba","linhas":"Linhas"})
                st.dataframe(df_abas, use_container_width=True, hide_index=True)
            else:
                st.info("Sem info de abas.")

        # Propriedades detalhado
        st.markdown("**Propriedades no PropertiesService (contadores por prefixo):**")
        contadores = diag_as.get("contadores", {})
        outras = diag_as.get("outras", {})
        linhas_props = [{"Prefixo": k, "Qtd": v} for k, v in contadores.items()]
        if outras.get("count", 0) > 0:
            linhas_props.append({"Prefixo": "_outras (config/tokens)", "Qtd": outras.get("count", 0)})
        if linhas_props:
            df_p = pd.DataFrame(linhas_props)
            st.dataframe(df_p, use_container_width=True, hide_index=True)

        # Órfãs detalhado
        orfaos_dict = diag_as.get("orfaos", {}).get("por_prefixo", {})
        if orfaos_dict:
            with st.expander(f"👻 Ver {diag_as['orfaos']['total']} órfã(s) detalhado(s)"):
                for prefixo, dados in orfaos_dict.items():
                    st.markdown(f"**{prefixo}** — {dados.get('count', 0)} órfã(s)")
                    exemplos = dados.get("exemplos", [])
                    if exemplos:
                        st.code(", ".join(exemplos))

        # Fantasmas detalhado
        fantasmas = diag_as.get("fantasmas", {})
        if fantasmas.get("total", 0) > 0:
            with st.expander(f"🕯️ Ver {fantasmas['total']} flag(s)-fantasma"):
                if fantasmas.get("lemb_sem_pendente"):
                    st.markdown("**Lembretes sem pendente_ correspondente:**")
                    st.code("\n".join(fantasmas["lemb_sem_pendente"]))
                if fantasmas.get("ind_lemb_sem_pendente"):
                    st.markdown("**ind_lemb sem ind_pendente_ correspondente:**")
                    st.code("\n".join(fantasmas["ind_lemb_sem_pendente"]))

        # Inconsistências detalhado
        incons = diag_as.get("inconsistencias", {})
        if incons.get("total", 0) > 0:
            with st.expander(f"🧩 Ver {incons['total']} inconsistência(s) status × props"):
                df_i = pd.DataFrame(incons.get("exemplos", []))
                if not df_i.empty:
                    df_i = df_i.rename(columns={"tel":"Telefone","tipo":"Tipo"})
                    st.dataframe(df_i, use_container_width=True, hide_index=True)

        # Dual-write status
        dw = diag_as.get("dual_write_supabase", {})
        if not dw.get("ativo"):
            st.error(f"🔴 **Dual-write Supabase DESLIGADO** — SUPABASE_DUAL_WRITE_ATIVO != 'true' no PropertiesService.")
        elif not dw.get("url_configurada") or not dw.get("key_configurada"):
            st.error(f"🔴 **Config Supabase incompleta** — URL={'✅' if dw.get('url_configurada') else '❌'}, KEY={'✅' if dw.get('key_configurada') else '❌'}")
        else:
            st.success("✅ Dual-write Supabase ativo e configurado.")

    st.divider()

    # =========================================================================
    # SEÇÃO 5: 📊 EXTRAS
    # =========================================================================
    st.markdown("### 📊 Extras")

    col_v1, col_v2 = st.columns(2)

    with col_v1:
        st.markdown("**📅 Volume de mensagens (últimos 30 dias)**")
        volumes = diag_sb.get("volumes_30d", [])
        if volumes:
            df_v = pd.DataFrame(volumes)
            df_v["dia"] = pd.to_datetime(df_v["dia"])
            fig = px.bar(df_v, x="dia", y="total",
                         color_discrete_sequence=["#5BC0BE"])
            fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10),
                              xaxis_title=None, yaxis_title=None)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados de volumes.")

    with col_v2:
        st.markdown("**🕐 Heatmap por hora (últimos 7 dias)**")
        heatmap = diag_sb.get("heatmap_horas_7d", [])
        if heatmap:
            df_h = pd.DataFrame(heatmap)
            # completa horas ausentes com 0
            todas_horas = pd.DataFrame({"hora": range(24)})
            df_h = todas_horas.merge(df_h, on="hora", how="left").fillna(0)
            df_h["total"] = df_h["total"].astype(int)
            fig = px.bar(df_h, x="hora", y="total",
                         color_discrete_sequence=["#a855f7"])
            fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10),
                              xaxis_title="Hora do dia (0-23)", yaxis_title=None,
                              xaxis=dict(tickmode='linear', tick0=0, dtick=2))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sem dados do heatmap.")

    st.markdown("**🔁 Clientes mais recorrentes (>3 interações em 30 dias)**")
    recorrentes = diag_sb.get("clientes_recorrentes", [])
    if recorrentes:
        df_r = pd.DataFrame(recorrentes)
        df_r = df_r.rename(columns={"telefone":"Telefone","nome":"Cliente","vezes":"Interações"})
        st.dataframe(df_r.head(30), use_container_width=True, hide_index=True)
        if len(df_r) > 30:
            st.caption(f"Mostrando 30 de {len(df_r)} clientes recorrentes.")
    else:
        st.info("Nenhum cliente com >3 interações em 30 dias.")
