"""
==============================================================================
ROBO FIDELIZACAO (FID) - disparo das boas-vindas, uma aba por unidade
==============================================================================
FID-06 v1 (26/09/2026) - degrau 2 do FID.
FID-08 v1 (29/09/2026) - o numero de alerta e editado AQUI, na tela de disparo,
  e cada lote herda o numero da hora do disparo, pra sempre (fid_trocar_alerta,
  fid_disparos_historico.telefone_alerta, webhook-fid v5.2). Titulo do erro corrigido.
FID-05 v1 (29/09/2026) - bloco "Pendencias" no topo da aba: quem travou no meio do
  fluxo, por unidade (fid_pendencias). SO LISTA - nenhum botao de reenvio (Carlos,
  29/09). O unico conserto automatico (religar o relogio da pergunta) roda no banco,
  pelo pg_cron, e aparece aqui como registro.
FID-05 v2 (29/09/2026) - Carlos: "isso nao e pendencia, e erro" e "fica numa aba nova
  Relatorio, igual o do Pos". A lista SAI da tela de disparo e vai para a aba
  "Relatorio" (render_aba_fid_relatorio), junto com o historico dos disparos.
FID-10 v1 (29/09/2026) - cada aba de unidade ganha 2 sub-abas: "Disparo" (a tela de
  sempre, intocada) e "Indicacoes": participantes, indicacoes recebidas por contato,
  FECHOU / NAO FECHOU (manda o template de aviso) e sessoes a agendar. Relatorio
  ganha AVISO_NAO_SAIU e AVISO_NAO_ENTREGUE.
FID-11 v1 (29/09/2026) - Relatorio ganha CARTAO_EM_TEXTO (a imagem do cartao falhou e o
  robo mandou o texto no lugar). Nada mais muda.
FID-09 v2 (29/09/2026) - o que e SEGURO reenviar, o robo conserta SOZINHO (pergunta,
  anexo, alerta, boas-vindas da FILA — webhook ?conserto=1) e aparece no Relatorio como
  "Consertado sozinho". Botao ("🛠️ Resolver um erro") so para o que PODE ter saido ou
  erro que se repete: disparar boas-vindas / reenviar aviso (pagos, com "conferi no
  WhatsApp Manager") e "resolvido" (so esconde). Quem decide se pode e o banco.
FID-09 v2.2 (29/09/2026) - Relatorio ganha CARTAO_NAO_ENTREGUE (a imagem do cartao saiu e
  a Meta falhou depois; o robo manda em texto sozinho).

Fluxo:
  1. Le fid_config da unidade (ativo, telefone_alerta) + modo_manutencao
  2. Upload XLSX: Telefone + Nome (ou Cliente). Vendedor/Servicos/Situacao opcionais
  3. Telefone pela regra de 25/09 (MEDICAO-TELEFONE §6) + o lixo do UNO (BUG-04)
  4. Previa: quem entra e quem pula, e por que (PREVISAO - quem decide e o banco)
  5. Travas: horario, janela 24h do alerta (checkbox, igual ao Pos), confirmacao dupla
  6. fid_criar_lote (1 transacao) -> para cada FILA do lote:
       fid_reservar_participante -> template Meta -> fid_confirmar_envio | fid_registrar_erro

COPIA do aba_pos_disparar.py (cada item nasceu de um acidente de 13/07):
  trava de dupla execucao por UID - erro isolado por cliente - 1 s entre envios -
  confirmacao dupla - wamid guardado na hora em que sai.
NAO COPIA a limpeza de telefone do Pos (nao poe 55, nao corta o lixo do UNO).

Segredos (st.secrets), NUNCA impressos:
  SUPABASE_URL, SUPABASE_KEY (tem que ser a service_role - FID-06 §4 item 1),
  TOKEN_META_FID (token do app Robo FID Maislaser; expires_at = 0, medido 25/09)
==============================================================================
"""

import re
import json
import time
import urllib.parse
import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))

PHONE_ID_FID     = "1283285444875792"
NUMERO_FID       = "5511925039610"                         # (11) 92503-9610
TEMPLATE_NOME    = "maislaser_fidelizacao_boasvindas_v1"   # APPROVED, medido 26/09
TEMPLATE_LANG    = "pt_BR"
META_API         = "v23.0"                                 # a mesma da webhook-fid
HORA_INICIO      = 8                                       # espelha o default do Pos
HORA_FIM         = 19
DIAS_REINSCRICAO = 60                                      # espelho do fid_criar_lote
VERSAO_ABA       = "FID-09 v2"
TEMPLATE_FECHOU     = "maislaser_fid_indicacao_fechou_v1"      # Ativo · Servicos (29/09)
TEMPLATE_NAO_FECHOU = "maislaser_fid_indicacao_nao_fechou_v1"  # Ativo · Servicos (29/09)

UNIDADE_ROTULO = {"MOGI": "Mogi das Cruzes", "SUZANO": "Suzano"}

# FID-05: tipo da fid_pendencias -> (como aparece, o que fazer) na aba Relatorio.
# A ORDEM desta lista e a ordem na tela: o que deixa a cliente sem resposta vem primeiro.
PENDENCIAS = [
    ("ALERTA_NAO_SAIU", "🔔 Recepção NÃO foi avisada",
     "Ela disse que TEM DÚVIDA e o aviso à recepção não saiu. Chamar a cliente agora."),
    ("ALERTA_NAO_ENTREGUE", "🔔 Aviso à recepção não chegou",
     "Ela disse que TEM DÚVIDA e a Meta não confirmou a entrega do aviso (o número de "
     "alerta estava sem a janela de 24 h?). Chamar a cliente."),
    ("ANEXOS_INCOMPLETOS", "📎 Faltou anexo",
     "Ela tocou em Pode enviar e não recebeu tudo (ver Detalhe). Mandar o que faltou "
     "pelo WhatsApp da unidade."),
    ("PERGUNTA_NAO_SAIU", "❓ A pergunta não saiu",
     "A Meta recusou o \"Recebeu tudo certinho?\". Falar com ela para confirmar."),
    ("PERGUNTA_NAO_ENTREGUE", "❓ A pergunta não chegou",
     "A Meta aceitou o \"Recebeu tudo certinho?\" e depois falhou. Falar com ela."),
    ("PERGUNTA_JANELA_FECHADA", "⌛ A pergunta passou das 24 h",
     "Não deu tempo de perguntar dentro da janela da Meta. Falar com ela se quiser confirmar."),
    ("PERGUNTA_ATRASADA", "⏱️ A pergunta está atrasada",
     "O relógio da pergunta ligou e ela não saiu (o relógio do FID-07 parou?). "
     "Avisar o Carlos e falar com ela."),
    ("PERGUNTA_NUNCA_PROGRAMADA", "❓ A pergunta nunca foi programada",
     "Ela tocou em Pode enviar e a pergunta não foi programada (a varredura está "
     "parada?). Avisar o Carlos e falar com ela."),
    ("AVISO_NAO_SAIU", "🎁 O aviso da indicação não saiu",
     "A equipe marcou a indicação (ver Detalhe) e a mensagem para quem indicou não saiu. "
     "PAROU_NO_MEIO ou TALVEZ_SAIU: conferir no WhatsApp Manager antes. Avisar a cliente "
     "pelo WhatsApp da unidade. NÃO marcar de novo."),
    ("AVISO_NAO_ENTREGUE", "🎁 O aviso da indicação não chegou",
     "A Meta aceitou e depois falhou (ver Detalhe). Avisar a cliente pelo WhatsApp da unidade."),
    ("FILA_PARADA", "📭 O disparo parou antes dela",
     "O envio foi interrompido (F5, aba fechada, reboot) e ela ficou na fila sem "
     "receber. Subir a planilha de novo e disparar."),
    ("TEMPLATE_NAO_ENTREGUE", "📵 As boas-vindas não chegaram",
     "A Meta aceitou e depois falhou (ver Detalhe). A cliente não recebeu nada. Falar "
     "com ela por outro canal."),
    ("RESERVADO_PRESO", "⏳ Disparo parou no meio",
     "O template PODE ter saído. Conferir no WhatsApp Manager antes de qualquer coisa. "
     "NÃO redisparar."),
    ("ERRO_NO_DISPARO", "❌ Erro no disparo",
     "NAO_SAIU: não chegou. TALVEZ_SAIU: conferir no WhatsApp Manager antes de "
     "qualquer coisa. Reenvio é caso a caso."),
    ("CARTAO_EM_TEXTO", "🖼️ Cartão saiu em texto",
     "A imagem do cartão não saiu e o robô mandou o texto no lugar — a cliente recebeu a "
     "informação. Se aparecer mais de uma vez, avisar o Carlos (ver Detalhe: etapa e erro)."),
    ("CARTAO_NAO_ENTREGUE", "🖼️ O cartão não chegou",
     "A Meta aceitou a imagem do cartão e depois falhou: a cliente não recebeu nada. O robô "
     "manda o mesmo conteúdo em texto sozinho (1 vez, com a janela de 24 h aberta). Se "
     "continuar aqui, avisar a cliente pelo WhatsApp da unidade."),
    ("CONSERTADO_SOZINHO", "🔧 Consertado sozinho",
     "O robô reenviou sozinho (ver Detalhe: o que e qual tentativa). Nada a fazer — é só "
     "registro. Se o erro original continuar na lista, as 2 tentativas não bastaram."),
    ("RELOGIO_RELIGADO", "🔧 Consertado sozinho",
     "A pergunta tinha ficado sem programar e a varredura religou. Nada a fazer — é só registro."),
]
PERIODOS = {"7 dias": 7, "30 dias": 30, "Tudo": 3650}


# ------------------------------------------------------------------ supabase
@st.cache_resource
def _sb() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def _estado(unidade):
    """fid_config da unidade + modo_manutencao. SEM cache de proposito: e o
    kill switch, e kill switch em cache de 30 s nao desliga por 30 s."""
    sb = _sb()
    r = (sb.table("fid_config")
           .select("ativo,telefone_alerta,telefone_contato")
           .eq("unidade", unidade).limit(1).execute())
    g = sb.table("configuracoes").select("modo_manutencao").eq("id", 1).limit(1).execute()
    cfg = r.data[0] if r.data else None
    manut = bool(g.data and g.data[0].get("modo_manutencao"))
    return cfg, manut


# ------------------------------------------------------------------ limpeza
def normalizar_telefone(bruto):
    """Regra de 25/09 (MEDICAO-TELEFONE §6) + o lixo do UNO (D-18 / BUG-04).
    Decide pelo TAMANHO, nunca pelo prefixo. NUNCA adivinha DDD (D-24).
    -> (telefone, None) | (None, motivo)"""
    if bruto is None or (isinstance(bruto, float) and pd.isna(bruto)):
        return None, "telefone vazio"
    s = str(bruto).strip()
    if s.endswith(".0"):
        s = s[:-2]
    d = re.sub(r"\D", "", s)
    if not d:
        return None, "telefone vazio"
    if len(d) == 14 and d.startswith("55") and d.endswith("0"):
        d = d[:-1]                  # BUG-04: o UNO exporta com um 0 a mais
    elif len(d) in (10, 11):
        d = "55" + d                # tem DDD, falta o 55
    if not re.fullmatch(r"55\d{10,11}", d):
        return None, f"telefone com {len(d)} dígitos — sem DDD ou inválido"
    return d, None


def primeiro_nome(bruto):
    """SO o primeiro nome - no template E no banco (FID-06 §0.2 D). O campo Nome
    do UNO e onde a recepcao digita CPF e anotacao de pagamento (D-16). Pegar a
    primeira palavra mata tudo de uma vez, sem prever cada tipo de lixo."""
    if bruto is None or (isinstance(bruto, float) and pd.isna(bruto)):
        return None
    s = re.sub(r"^_+", "", str(bruto).strip())
    s = re.sub(r"\([^)]*\)", " ", s)
    partes = s.split()
    if not partes:
        return None
    p = re.sub(r"[^A-Za-zÀ-ÿ'\-]", "", partes[0])
    return p.title() if p else None


def _txt(v, n):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s[:n] if s else None


def parsear(arquivo):
    """-> (validos, rejeitados, lidas, erro)"""
    try:
        df = pd.read_excel(arquivo, sheet_name=0, dtype=str)
    except Exception as e:
        return [], [], 0, f"Não consegui ler o XLSX: {e}"

    cols = {str(c).strip().lower(): c for c in df.columns}
    c_tel  = cols.get("telefone")
    c_nome = cols.get("nome") or cols.get("cliente")
    if not c_tel or not c_nome:
        return [], [], 0, ("A planilha precisa de uma coluna **Telefone** e uma coluna "
                           "**Nome** (ou **Cliente**). Colunas encontradas: "
                           + ", ".join(str(c) for c in df.columns))
    c_vend = cols.get("vendedor")
    c_serv = cols.get("serviços") or cols.get("servicos")
    c_sit  = cols.get("situação") or cols.get("situacao")

    validos, rejeitados, vistos = [], [], set()
    for i, row in df.iterrows():
        linha = int(i) + 2                       # linha 1 do Excel e o cabecalho
        tel, motivo = normalizar_telefone(row.get(c_tel))
        nome = primeiro_nome(row.get(c_nome))
        if motivo:
            rejeitados.append({"linha": linha, "telefone": _txt(row.get(c_tel), 40), "motivo": motivo})
            continue
        if not nome:
            rejeitados.append({"linha": linha, "telefone": tel, "motivo": "sem nome"})
            continue
        if tel in vistos:
            rejeitados.append({"linha": linha, "telefone": tel, "motivo": "repetida na planilha"})
            continue
        vistos.add(tel)
        validos.append({
            "telefone": tel,
            "nome":     nome,
            "vendedor": _txt(row.get(c_vend), 200) if c_vend else None,
            "servicos": _txt(row.get(c_serv), 2000) if c_serv else None,
            "situacao": _txt(row.get(c_sit), 100) if c_sit else None,
        })
    return validos, rejeitados, len(df), None


# ------------------------------------------------------------------ previa
def _prever(atual):
    """ESPELHO da tabela de decisao do fid_criar_lote (FID-06 §2.2). E PREVISAO:
    o banco decide de verdade, sob FOR UPDATE. Se divergir do SQL, a previa mente."""
    if atual is None:
        return "entra", "nova no programa"
    s = atual.get("status")
    if s == "FILA":
        return "entra", "estava na fila de outro lote"
    if s == "RESERVADO":
        return "pula", "em envio agora"
    if s == "ERRO":
        return "pula", "erro no envio anterior — reenvio é decisão caso a caso"
    if s == "ENVIADO" and not atual.get("respondeu_em"):
        return "pula", "ainda não respondeu as boas-vindas anteriores"
    if s == "ENVIADO":
        ref = atual.get("enviado_em") or atual.get("criado_em")
        limite = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=DIAS_REINSCRICAO)
        if ref is not None and pd.to_datetime(ref, utc=True) > limite:
            return "pula", f"recebeu há menos de {DIAS_REINSCRICAO} dias"
        return "entra", f"reinscrita — ciclo {int(atual.get('ciclo') or 1) + 1}"
    return "pula", f"estado desconhecido ({s})"


def prever(validos):
    tels = [v["telefone"] for v in validos]
    atuais = {}
    for i in range(0, len(tels), 100):
        r = (_sb().table("fid_participantes")
               .select("telefone,status,respondeu_em,enviado_em,criado_em,ciclo")
               .in_("telefone", tels[i:i + 100]).execute())
        for a in (r.data or []):
            atuais[a["telefone"]] = a
    linhas = []
    for v in validos:
        acao, motivo = _prever(atuais.get(v["telefone"]))
        linhas.append({**v, "acao": acao, "motivo": motivo})
    return linhas


# ------------------------------------------------------------------ meta
def enviar_template(telefone, nome, template=TEMPLATE_NOME):
    """-> (wamid, None) | (None, (codigo, mensagem, talvez_saiu))
    talvez_saiu=True quando NAO da para afirmar que a Meta nao entregou
    (timeout, 5xx, 2xx sem wamid). E a armadilha do BIA-02."""
    try:
        token = st.secrets["TOKEN_META_FID"]
    except Exception:
        return None, ("SEM_TOKEN", "TOKEN_META_FID ausente em st.secrets", False)
    url = f"https://graph.facebook.com/{META_API}/{PHONE_ID_FID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": TEMPLATE_LANG},
            "components": [{"type": "body",
                            "parameters": [{"type": "text", "text": (nome or "cliente")[:60]}]}],
        },
    }
    try:
        r = requests.post(url, json=payload, timeout=15,
                          headers={"Authorization": f"Bearer {token}"})
    except requests.exceptions.Timeout:
        return None, ("TIMEOUT", "a Meta não respondeu em 15 s", True)
    except Exception as e:
        return None, ("EXCECAO", str(e)[:500], True)
    if 200 <= r.status_code < 300:
        try:
            return r.json()["messages"][0]["id"], None
        except Exception:
            return None, ("2XX_SEM_WAMID", r.text[:500], True)
    return None, (f"HTTP_{r.status_code}", r.text[:500], r.status_code >= 500)


def confirmar(telefone, wamid):
    """fid_confirmar_envio e idempotente com o MESMO wamid (FID-02 v3): repetir e
    seguro. -> (ok, erro)"""
    erro = None
    for tentativa in range(3):
        try:
            d = _sb().rpc("fid_confirmar_envio",
                          {"p_telefone": telefone, "p_wamid": wamid}).execute().data
            if isinstance(d, dict) and d.get("ok"):
                return True, None
            return False, f"o banco recusou: {d}"   # ENVIADO_FORA_DE_ORDEM - repetir nao conserta
        except Exception as e:
            erro = str(e)[:300]
            if "23505" in erro:                     # indice unico - FID-04 §0.2 regra 2
                return False, "409/23505 — wamid já existe em outra linha: " + erro
            time.sleep(1.5 * (tentativa + 1))
    return False, erro


# ------------------------------------------------------------------ disparo
def _executar(unidade, validos, lidas, arquivo_nome, k):
    uid = datetime.now(TZ_SP).isoformat()
    if st.session_state.get(k + "em_andamento"):
        st.warning("⚠️ Disparo já em andamento. Aguarde, ou recarregue (F5) se travou.")
        return
    st.session_state[k + "em_andamento"] = uid
    resumo = {"lote_id": None, "alerta": None, "enviados": 0, "erros": [], "presos": [],
              "pulados_banco": [], "tomados_por_outro": [], "falhas_inesperadas": []}
    try:
        # 1. lote + participantes, numa transacao so
        r = _sb().rpc("fid_criar_lote", {
            "p_unidade": unidade, "p_arquivo": arquivo_nome, "p_template": TEMPLATE_NOME,
            "p_linhas_lidas": int(lidas), "p_participantes": validos,
            "p_criado_por": "painel",
        }).execute()
        lote = r.data
        if not (isinstance(lote, dict) and lote.get("ok")):
            st.error(f"❌ O banco não criou o lote: {lote}")
            return
        resumo["lote_id"] = lote["lote_id"]
        resumo["alerta"] = lote.get("telefone_alerta")      # FID-08: o que ESTE lote herdou
        resumo["pulados_banco"] = lote.get("puladas") or []

        # 2. quem ficou na FILA deste lote - relido do banco, que e quem manda
        fila = (_sb().table("fid_participantes").select("telefone,nome")
                  .eq("lote_id", lote["lote_id"]).eq("status", "FILA")
                  .order("telefone").execute().data) or []
        prog = st.progress(0.0)
        txt = st.empty()

        # 3. uma por uma: carimba, manda, confirma
        for i, p in enumerate(fila):
            tel = p["telefone"]
            txt.text(f"Enviando {i + 1}/{len(fila)} — {p['nome']}…")
            prog.progress((i + 1) / len(fila))
            wamid = None
            try:
                res = _sb().rpc("fid_reservar_participante", {"p_telefone": tel}).execute().data
                if not (isinstance(res, dict) and res.get("ok")):
                    resumo["tomados_por_outro"].append({"telefone": tel, "retorno": str(res)[:200]})
                    continue
                wamid, err = enviar_template(tel, p["nome"])
                if wamid:
                    ok, msg = confirmar(tel, wamid)
                    if ok:
                        resumo["enviados"] += 1
                    else:
                        resumo["presos"].append({"telefone": tel, "wamid": wamid, "erro": msg})
                else:
                    codigo, mensagem, talvez = err
                    cod = ("TALVEZ_SAIU:" if talvez else "NAO_SAIU:") + codigo
                    try:
                        _sb().rpc("fid_registrar_erro", {"p_telefone": tel, "p_codigo": cod,
                                                         "p_mensagem": mensagem}).execute()
                    except Exception as e2:
                        mensagem += f" [e o erro NÃO foi gravado no banco: {str(e2)[:150]}]"
                    resumo["erros"].append({"telefone": tel, "codigo": cod,
                                            "mensagem": mensagem[:300]})
            except Exception as e:
                # se ja existe wamid, a mensagem SAIU - isso nao pode sumir da tela
                item = {"telefone": tel, "erro": str(e)[:300]}
                if wamid:
                    item["wamid"] = wamid
                    resumo["presos"].append(item)
                else:
                    resumo["falhas_inesperadas"].append(item)
            finally:
                time.sleep(1.0)                    # Pos, 13/07: 0,3 s era pouco

        st.session_state[k + "resumo"] = resumo
        st.session_state[k + "finalizado"] = True
    finally:
        if st.session_state.get(k + "em_andamento") == uid:
            st.session_state[k + "em_andamento"] = None


def _tela_resumo(k):
    r = st.session_state.get(k + "resumo", {})
    st.markdown("### 🎉 Disparo finalizado")
    c1, c2, c3 = st.columns(3)
    c1.metric("✅ Enviados", r.get("enviados", 0))
    c2.metric("❌ Erros", len(r.get("erros", [])))
    c3.metric("⏭️ Pulados pelo banco", len(r.get("pulados_banco", [])))
    st.caption(f"Lote {r.get('lote_id')} · alerta deste lote: +{r.get('alerta')} · {VERSAO_ABA}")

    if r.get("presos"):
        st.error(
            "🔴 **A mensagem SAIU para estas clientes, mas o banco NÃO gravou.** "
            "**NÃO redispare** — vai duplicar. Enquanto não corrigir, o clique delas "
            "não acha a linha. A correção é a mesma função, e repetir é seguro:")
        st.code("\n".join(f"select fid_confirmar_envio('{p['telefone']}', '{p['wamid']}');"
                          for p in r["presos"] if p.get("wamid")), language="sql")
        st.dataframe(pd.DataFrame(r["presos"]), use_container_width=True, hide_index=True)
    if r.get("erros"):
        # FID-08: o titulo dizia TALVEZ_SAIU para QUALQUER erro. Agora conta cada um.
        talvez = sum(1 for e in r["erros"] if str(e.get("codigo", "")).startswith("TALVEZ_SAIU"))
        nao = len(r["erros"]) - talvez
        partes = []
        if talvez:
            partes.append(f"{talvez} TALVEZ_SAIU (não dá para afirmar que não chegou)")
        if nao:
            partes.append(f"{nao} NAO_SAIU (não chegou)")
        with st.expander(f"❌ {len(r['erros'])} erro(s) — " + " · ".join(partes)):
            st.dataframe(pd.DataFrame(r["erros"]), use_container_width=True, hide_index=True)
    if r.get("pulados_banco"):
        with st.expander(f"⏭️ {len(r['pulados_banco'])} pulada(s) pelo banco"):
            st.dataframe(pd.DataFrame(r["pulados_banco"]), use_container_width=True, hide_index=True)
    if r.get("tomados_por_outro"):
        with st.expander(f"↪️ {len(r['tomados_por_outro'])} já tinham saído da fila (outro disparo pegou antes)"):
            st.dataframe(pd.DataFrame(r["tomados_por_outro"]), use_container_width=True, hide_index=True)
    if r.get("falhas_inesperadas"):
        st.warning("⚠️ Falhas fora do previsto — **podem ter ficado presas em RESERVADO** "
                   "(é o que a FID-05 vai varrer). Nenhuma delas recebeu mensagem:")
        st.dataframe(pd.DataFrame(r["falhas_inesperadas"]), use_container_width=True, hide_index=True)

    if st.button("🔄 Fazer novo disparo", type="primary", use_container_width=True,
                 key=k + "btn_novo"):
        st.session_state[k + "reset"] = True
        st.rerun()


# ------------------------------------------------------------------ tela
def _hora_sp(iso):
    if not iso:
        return "—"
    try:
        return pd.to_datetime(iso, utc=True).tz_convert(TZ_SP).strftime("%d/%m %H:%M")
    except Exception:
        return str(iso)


def _linhas_erros(itens, com_unidade):
    """FID-05: itens da fid_pendencias -> linhas da tabela, na ordem de gravidade."""
    ordem = {t: n for n, (t, _, _) in enumerate(PENDENCIAS)}
    textos = {t: (r, f) for t, r, f in PENDENCIAS}
    # mais recente primeiro, depois a ordem de gravidade (sort estavel)
    itens = sorted(itens, key=lambda i: str(i.get("desde") or ""), reverse=True)
    itens = sorted(itens, key=lambda i: ordem.get(i.get("tipo"), 99))
    linhas = []
    for i in itens:
        rotulo, fazer = textos.get(i.get("tipo"), (i.get("tipo"), "—"))
        linha = {"Erro": rotulo}
        if com_unidade:
            linha["Unidade"] = UNIDADE_ROTULO.get(i.get("unidade"), i.get("unidade"))
        linha.update({
            "Nome": i.get("nome"),
            "Telefone": i.get("telefone"),
            "Desde": _hora_sp(i.get("desde")),
            "O que fazer": fazer,
            "Detalhe": json.dumps(i.get("detalhe"), ensure_ascii=False)[:300],
        })
        linhas.append(linha)
    return linhas


def render_aba_fid_relatorio():
    """FID-05 v2: aba Relatorio do FID - erros de envio + historico dos disparos.
    Falhar uma leitura nao derruba a outra. FID-09: os botoes ficam no bloco
    "Resolver um erro", abaixo da tabela (_resolver)."""
    k = "fid_rel_"
    st.markdown("## 📋 Relatório — Fidelização")
    c1, c2 = st.columns(2)
    with c1:
        un_rot = st.radio("Unidade", ["Todas", "Mogi", "Suzano"], horizontal=True,
                          key=k + "unidade")
    with c2:
        per_rot = st.radio("Período", list(PERIODOS), horizontal=True, key=k + "periodo")
    unidades = {"Todas": ["MOGI", "SUZANO"], "Mogi": ["MOGI"], "Suzano": ["SUZANO"]}[un_rot]
    dias = PERIODOS[per_rot]

    falhas = []
    erros = []
    for u in unidades:
        try:
            d = _sb().rpc("fid_pendencias", {"p_unidade": u, "p_dias": dias}).execute().data
        except Exception as e:
            falhas.append(f"erros de {u}: {str(e)[:150]}")
            continue
        if not isinstance(d, list):
            falhas.append(f"erros de {u}: {str(d)[:150]}")
            continue
        for i in d:
            i["unidade"] = u
            erros.append(i)

    lotes = []
    try:
        desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lotes = (_sb().table("fid_disparos_historico")
                   .select("id,unidade,arquivo_nome,linhas_lidas,linhas_validas,"
                           "linhas_puladas,telefone_alerta,criado_em")
                   .in_("unidade", unidades).gte("criado_em", desde)
                   .order("criado_em", desc=True).limit(500).execute().data) or []
    except Exception as e:
        falhas.append(f"disparos: {str(e)[:150]}")

    for f in falhas:
        st.warning(f"⚠️ Não consegui ler {f}")

    reais = [i for i in erros if i.get("tipo") not in ("RELOGIO_RELIGADO", "CONSERTADO_SOZINHO")]
    m1, m2, m3 = st.columns(3)
    m1.metric("📤 Disparos", len(lotes))
    m2.metric("👥 Clientes nos disparos", sum(int(l.get("linhas_validas") or 0) for l in lotes))
    m3.metric("❌ Erros de envio", len(reais))

    st.markdown("### ❌ Erros de envio")
    if erros:
        st.dataframe(pd.DataFrame(_linhas_erros(erros, len(unidades) > 1)),
                     use_container_width=True, hide_index=True)
        st.caption("\"Consertado sozinho\" é só registro. Um erro sai da lista quando a "
                   "situação da cliente muda, quando é marcado como resolvido, ou quando "
                   "passa do período escolhido.")
        _resolver(erros)
    else:
        st.success("Nenhum erro de envio no período.")

    st.markdown("### 📋 Disparos")
    if lotes:
        st.dataframe(pd.DataFrame([{
            "Data": _hora_sp(l.get("criado_em")),
            "Unidade": UNIDADE_ROTULO.get(l.get("unidade"), l.get("unidade")),
            "Arquivo": l.get("arquivo_nome"),
            "Linhas": l.get("linhas_lidas"),
            "Entraram": l.get("linhas_validas"),
            "Pularam": l.get("linhas_puladas"),
            "Alerta do lote": ("+" + l["telefone_alerta"]) if l.get("telefone_alerta") else "—",
            "Lote": l.get("id"),
        } for l in lotes]), use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum disparo no período.")


# ------------------------------------------------------------------ FID-09: resolver
ROTULO_ACAO = {
    "boas_vindas": "🔁 Disparar as boas-vindas de novo (template pago)",
    "aviso":       "🔁 Reenviar o aviso da indicação (template pago)",
    "resolvido":   "✅ Marcar como resolvido",
}
# FID-09 v2: pergunta, anexo, alerta e FILA o robo conserta sozinho (ate 2 vezes).
# Botao so para o que PODE ter saido ou erro que se repete. Todos ganham "resolvido".
ACOES = {
    "ERRO_NO_DISPARO": ["boas_vindas"],
    "RESERVADO_PRESO": ["boas_vindas"], "TEMPLATE_NAO_ENTREGUE": ["boas_vindas"],
    "AVISO_NAO_SAIU": ["aviso"], "AVISO_NAO_ENTREGUE": ["aviso"],
}
PAGAS = {"boas_vindas", "aviso"}
MOTIVO_BANCO = {
    "confirme_no_manager": "pode ter saído. Confira no WhatsApp Manager e marque a caixa "
                           "\"conferi que NÃO chegou\".",
    "disparo_em_andamento": "tem um disparo em andamento para ela (menos de 30 min). Espere.",
    "aviso_em_andamento": "tem um aviso sendo enviado agora (menos de 10 min). Espere.",
    "ja_respondeu": "ela já recebeu e tocou nas boas-vindas.",
    "nao_falhou": "a Meta não registrou falha nas boas-vindas dela.",
    "aviso_nao_falhou": "a Meta não registrou falha nesse aviso.",
    "sem_participante": "a cliente não está mais no programa.",
    "indicacao_sem_decisao": "a indicação não está marcada.",
}


def _rpc(nome, args):
    try:
        d = _sb().rpc(nome, args).execute().data
    except Exception as e:
        return {"ok": False, "motivo": "banco: " + str(e)[:200]}
    return d if isinstance(d, dict) else {"ok": False, "motivo": f"banco devolveu {d}"}


def _recusa(d):
    m = d.get("motivo") if isinstance(d, dict) else d
    return "❌ Não fiz: " + MOTIVO_BANCO.get(m, str(m))


def _acao_boas_vindas(item, conferido):
    tel = item["telefone"]
    d = _rpc("fid_reenfileirar", {"p_telefone": tel, "p_por": "painel", "p_conferido": bool(conferido)})
    if not d.get("ok"):
        return "error", _recusa(d)
    # o mesmo caminho do disparo: carimba -> manda -> confirma (BIA-02)
    res = _rpc("fid_reservar_participante", {"p_telefone": tel})
    if not res.get("ok"):
        return "error", f"❌ Voltou para a fila, mas não consegui reservar: {res}. Tente de novo."
    wamid, err = enviar_template(tel, d.get("nome"))
    if wamid:
        ok, msg = confirmar(tel, wamid)
        if ok:
            return "success", "✅ Boas-vindas enviadas de novo."
        return "error", (f"🔴 SAIU, mas o banco não gravou ({msg}). **NÃO dispare de novo.** "
                         f"Correção: `select fid_confirmar_envio('{tel}', '{wamid}');`")
    codigo, mensagem, talvez = err
    cod = ("TALVEZ_SAIU:" if talvez else "NAO_SAIU:") + codigo
    _rpc("fid_registrar_erro", {"p_telefone": tel, "p_codigo": cod, "p_mensagem": mensagem})
    return "error", f"❌ Não saiu ({cod}: {mensagem[:200]}). Continua no Relatório."


def _acao_aviso(item, conferido):
    ind = (item.get("detalhe") or {}).get("indicacao")
    if not ind:
        return "error", "❌ Este erro não traz a indicação (Relatório antigo). Recarregue a página."
    d = _rpc("fid_reenviar_aviso", {"p_indicacao": int(ind), "p_por": "painel",
                                    "p_conferido": bool(conferido)})
    if not d.get("ok"):
        return "error", _recusa(d)
    quem = d["indicante"]
    tpl = TEMPLATE_FECHOU if d.get("tipo") == "FECHOU" else TEMPLATE_NAO_FECHOU
    wamid, err = enviar_template(quem["telefone"], quem["nome"], tpl)
    if wamid:
        ok, msg = _confirmar_aviso(d["aviso_id"], wamid)
        if ok:
            return "success", f"✅ Aviso reenviado para {quem['nome']}."
        return "error", (f"🔴 SAIU, mas o banco não gravou ({msg}). **NÃO reenvie.** Correção: "
                         f"`select fid_confirmar_aviso({d['aviso_id']}, '{wamid}');`")
    codigo, mensagem, talvez = err
    cod = ("TALVEZ_SAIU:" if talvez else "NAO_SAIU:") + codigo
    _rpc("fid_registrar_erro_aviso", {"p_aviso": d["aviso_id"], "p_codigo": cod, "p_mensagem": mensagem})
    return "error", f"❌ Não saiu ({cod}: {mensagem[:200]}). Continua no Relatório."


def _resolver(itens):
    """FID-09: o bloco de botoes. Um erro por vez: escolhe, ve o que da para fazer,
    confirma. Quem diz se PODE e o banco; aqui so pergunta e mostra."""
    k = "fid_rel_res_"
    st.markdown("### 🛠️ Resolver um erro")
    msg = st.session_state.pop(k + "msg", None)
    if msg:
        getattr(st, msg[0])(msg[1])

    textos = {t: r for t, r, _ in PENDENCIAS}
    chave = lambda i: f"{i.get('tipo')}|{i.get('telefone')}|{i.get('desde')}"
    op = {chave(i): i for i in itens}
    if st.session_state.get(k + "sel") not in op:
        st.session_state.pop(k + "sel", None)
    sel = st.selectbox("Erro", list(op), key=k + "sel",
                       format_func=lambda c: (f"{textos.get(op[c]['tipo'], op[c]['tipo'])} · "
                                              f"{op[c].get('nome')} · +{op[c].get('telefone')} · "
                                              f"{_hora_sp(op[c].get('desde'))}"))
    item = op[sel]
    acoes = ACOES.get(item.get("tipo"), []) + ["resolvido"]

    try:
        cfg, manut = _estado(item.get("unidade"))
    except Exception:
        cfg, manut = None, True
    hora = datetime.now(TZ_SP).hour
    pode_enviar = bool(cfg and cfg.get("ativo") and not manut and HORA_INICIO <= hora < HORA_FIM)
    if not pode_enviar and len(acoes) > 1:
        st.caption(f"Reenvio bloqueado agora (FID desligado, manutenção, ou fora de "
                   f"{HORA_INICIO}h–{HORA_FIM}h). \"Resolvido\" continua valendo.")

    cols = st.columns(len(acoes))
    for c, a in zip(cols, acoes):
        with c:
            if st.button(ROTULO_ACAO[a], key=k + "btn_" + a, use_container_width=True,
                         disabled=(a != "resolvido" and not pode_enviar)):
                st.session_state[k + "conf"] = (sel, a)
                st.rerun()

    conf = st.session_state.get(k + "conf")
    if not conf or conf[0] != sel:
        return
    acao = conf[1]
    conferido, obs = False, None
    if acao in PAGAS:
        st.warning(f"⚠️ {ROTULO_ACAO[acao]} para **{item.get('nome')}** — é template **pago**. "
                   f"Se a mensagem anterior PODE ter saído, confira no WhatsApp Manager antes.")
        conferido = st.checkbox("Conferi no WhatsApp Manager que a mensagem anterior NÃO chegou",
                                key=k + "conferi")
    elif acao == "resolvido":
        obs = st.text_input("O que foi feito (opcional)", key=k + "obs")
    s1, s2 = st.columns(2)
    with s1:
        if st.button("✅ Confirmar", type="primary", use_container_width=True, key=k + "btn_sim"):
            if st.session_state.get(k + "em_andamento"):
                st.warning("⚠️ Já está enviando. Aguarde.")
                return
            st.session_state[k + "em_andamento"] = True
            try:
                if acao == "boas_vindas":
                    r = _acao_boas_vindas(item, conferido)
                elif acao == "aviso":
                    r = _acao_aviso(item, conferido)
                else:
                    d = _rpc("fid_marcar_resolvido", {"p_tipo": item["tipo"], "p_telefone": item["telefone"],
                                                      "p_desde": item["desde"], "p_por": "painel",
                                                      "p_obs": obs or None})
                    r = (("success", "✅ Marcado como resolvido — saiu da lista.") if d.get("ok")
                         else ("error", _recusa(d)))
            finally:
                st.session_state[k + "em_andamento"] = False
                st.session_state.pop(k + "conf", None)
                st.session_state.pop(k + "conferi", None)
            st.session_state[k + "msg"] = r
            st.rerun()
    with s2:
        if st.button("Cancelar", use_container_width=True, key=k + "btn_nao"):
            st.session_state.pop(k + "conf", None)
            st.rerun()


def _alerta_editar(unidade, alerta, k):
    """FID-08: o numero de alerta e editado AQUI, na tela de disparo (Carlos, 29/09).
    Cada disparo herda o numero que estiver aqui na hora, pra sempre: trocar so vale
    para os PROXIMOS disparos. A troca e do banco (fid_trocar_alerta, loga de/para)."""
    if st.session_state.get(k + "alerta_msg"):
        st.success(st.session_state.pop(k + "alerta_msg"))
    with st.expander(f"🔔 Número de alerta desta unidade: +{alerta} — trocar"):
        st.caption("Vale só para os **próximos** disparos. Quem já recebeu continua "
                   "alertando o número do disparo dela.")
        bruto = st.text_input("Novo número (com DDD)", key=k + "alerta_novo")
        if st.button("💾 Salvar número de alerta", key=k + "btn_alerta"):
            novo, motivo = normalizar_telefone(bruto)
            if motivo:
                st.error(f"❌ {motivo}")
                return
            try:
                res = _sb().rpc("fid_trocar_alerta", {"p_unidade": unidade, "p_telefone": novo,
                                                      "p_por": "painel"}).execute().data
            except Exception as e:
                res = {"ok": False, "motivo": str(e)[:200]}
            if not (isinstance(res, dict) and res.get("ok")):
                st.error(f"❌ Não troquei: {res}")
                return
            if not res.get("mudou"):
                st.info("Esse já é o número de alerta.")
                return
            # numero novo = a janela de 24 h confirmada era do numero ANTIGO
            st.session_state.pop(k + "janela_ok", None)
            st.session_state.pop(k + "confirmar", None)
            st.session_state[k + "alerta_msg"] = (f"✅ Alerta trocado: +{res.get('de')} → "
                                                  f"+{res.get('para')}. Vale a partir do próximo disparo.")
            st.rerun()


def _render(unidade):
    # st.tabs renderiza as DUAS abas em toda execucao: TODA chave e prefixada
    # pela unidade, senao Mogi e Suzano dividem estado (FID-06 §4 item 8)
    k = f"fid_{unidade}_"
    rotulo = UNIDADE_ROTULO[unidade]

    if st.session_state.get(k + "reset"):
        st.session_state[k + "gen"] = st.session_state.get(k + "gen", 0) + 1
        for chave in list(st.session_state.keys()):
            if chave.startswith(k) and chave != k + "gen":
                del st.session_state[chave]
        st.rerun()

    st.markdown(f"## 💚 Fidelização — {rotulo}")
    st.caption(f"Boas-vindas do programa · template `{TEMPLATE_NOME}` · {VERSAO_ABA}")

    if st.session_state.get(k + "finalizado"):
        _tela_resumo(k)
        return

    try:
        cfg, manut = _estado(unidade)
    except Exception as e:
        st.error(f"⚠️ Não consegui ler a configuração do FID: {e}")
        return
    if not cfg:
        st.error(f"🔴 Não existe linha em `fid_config` para {unidade}.")
        return
    if not cfg.get("ativo"):
        st.error(f"🔴 **FID desligado para {rotulo}** — `fid_config.ativo = false`.")
        return
    if manut:
        st.error("🔴 **MODO MANUTENÇÃO ATIVO** — todos os robôs estão pausados.")
        return

    _alerta_editar(unidade, cfg["telefone_alerta"], k)

    hora = datetime.now(TZ_SP).hour
    dentro = HORA_INICIO <= hora < HORA_FIM
    if not dentro:
        st.warning(f"⚠️ Fora do horário ({HORA_INICIO}h–{HORA_FIM}h). "
                   f"Dá para preparar, mas o envio fica bloqueado.")

    # ---- 1. planilha
    st.markdown("### 1. Planilha")
    st.caption("Obrigatório: **Telefone** e **Nome** (ou **Cliente**). Vendedor, Serviços e "
               "Situação são guardados se vierem. **Todo mundo da planilha entra** — sem "
               "filtro por Situação.")
    arq = st.file_uploader("XLSX de vendas", type=["xlsx", "xls"],
                           key=f"{k}upl_{st.session_state.get(k + 'gen', 0)}")
    if not arq:
        return

    validos, rejeitados, lidas, erro = parsear(arq)
    if erro:
        st.error(f"❌ {erro}")
        return

    # ---- 2. previa
    st.markdown("### 2. Prévia")
    try:
        linhas = prever(validos)
    except Exception as e:
        st.error(f"⚠️ Não consegui consultar quem já está no programa: {e}")
        return
    entram = [x for x in linhas if x["acao"] == "entra"]
    pulam  = [x for x in linhas if x["acao"] == "pula"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📄 Linhas lidas", lidas)
    c2.metric("✅ Vão receber", len(entram))
    c3.metric("⏭️ Vão pular", len(pulam))
    c4.metric("❌ Rejeitadas", len(rejeitados))
    st.caption("É **previsão**: quem decide de verdade é o banco, na hora do envio.")

    if entram:
        st.dataframe(pd.DataFrame([{"Nome no template": x["nome"], "Telefone": x["telefone"],
                                    "Por quê": x["motivo"]} for x in entram]),
                     use_container_width=True, hide_index=True)
    if pulam:
        with st.expander(f"⏭️ {len(pulam)} vão pular"):
            st.dataframe(pd.DataFrame([{"Nome": x["nome"], "Telefone": x["telefone"],
                                        "Por quê": x["motivo"]} for x in pulam]),
                         use_container_width=True, hide_index=True)
    if rejeitados:
        with st.expander(f"❌ {len(rejeitados)} rejeitada(s) pela planilha"):
            st.dataframe(pd.DataFrame(rejeitados), use_container_width=True, hide_index=True)

    if not entram:
        st.info("Ninguém para enviar nesta planilha.")
        return

    # ---- 3. travas
    st.markdown("### 3. Disparar")
    if not dentro:
        st.button("🚀 Disparar", disabled=True, use_container_width=True, key=k + "btn_bloq")
        return

    alerta = cfg["telefone_alerta"]
    st.warning(
        f"⚠️ **Abra a janela de 24 h do alerta antes de disparar.**\n\n"
        f"Quando uma cliente responder *\"tenho dúvida\"*, o robô avisa **+{alerta}**. "
        f"Esse aviso é mensagem livre: a Meta **só entrega** se esse número tiver mandado "
        f"qualquer mensagem para o robô **(11) 92503-9610** nas últimas 24 h.\n\n"
        f"⚠️ A caixa abaixo cobre as **próximas 24 h**, não para sempre. Alerta que "
        f"falhar fica registrado como `ALERTA_FALHOU`.")
    ca, cb = st.columns(2)
    with ca:
        texto = urllib.parse.quote(
            f"Manda um oi para o robô de fidelização: https://wa.me/{NUMERO_FID}")
        st.link_button(f"💬 Chamar +{alerta} no WhatsApp",
                       f"https://wa.me/{alerta}?text={texto}", use_container_width=True)
    with cb:
        st.link_button("🤖 Abrir o robô (mandar 'oi')",
                       f"https://wa.me/{NUMERO_FID}?text=oi", use_container_width=True)

    if not st.checkbox(f"✅ Confirmo que +{alerta} mandou mensagem para o robô nas últimas 24 h",
                       key=k + "janela_ok"):
        st.button(f"🚀 Disparar para {len(entram)}", disabled=True,
                  use_container_width=True, key=k + "btn_sem_janela")
        return

    if not st.session_state.get(k + "confirmar"):
        if st.button(f"🚀 Disparar para {len(entram)} cliente(s)", type="primary",
                     use_container_width=True, key=k + "btn_1"):
            st.session_state[k + "confirmar"] = True
            st.rerun()
        return

    st.warning(f"⚠️ Confirmar envio para **{len(entram)}** cliente(s) de **{rotulo}**?")
    cs, cn = st.columns(2)
    with cs:
        if st.button("✅ Sim, disparar agora", type="primary", use_container_width=True,
                     key=k + "btn_sim"):
            _executar(unidade, validos, lidas, arq.name, k)
            st.rerun()
    with cn:
        if st.button("❌ Cancelar", use_container_width=True, key=k + "btn_nao"):
            st.session_state[k + "confirmar"] = False
            st.rerun()


# ------------------------------------------------------------------ FID-10: indicacoes
ROTULO_STATUS = {"NOVA": "🆕 Nova", "FECHOU": "✅ Fechou", "NAO_FECHOU": "❌ Não fechou"}
MOTIVO_RECUSA = {
    "ja_fechou": "Essa indicação já está como FECHOU — não muda mais.",
    "indicacao_nao_existe": "Essa indicação não existe mais. Recarregue a página.",
    "decisao_invalida": "Decisão inválida.",
}


def _em_lotes(tabela, colunas, coluna, valores):
    linhas = []
    for i in range(0, len(valores), 100):
        linhas += (_sb().table(tabela).select(colunas).in_(coluna, valores[i:i + 100])
                     .order("id").execute().data) or []
    return sorted(linhas, key=lambda x: x["id"])


def _ler_indicacoes(unidade):
    """-> (indicacoes, participantes, selos, avisos, areas). SO LE."""
    sb = _sb()
    ind = (sb.table("fid_indicacoes")
             .select("id,indicante,nome,telefone,tem_whatsapp,ja_participante,status,"
                     "decidido_por,decidido_em,criado_em")
             .eq("unidade", unidade).order("criado_em", desc=True).limit(5000)
             .execute().data) or []
    part = (sb.table("fid_participantes").select("telefone,nome,status,enviado_em")
              .eq("unidade", unidade).limit(5000).execute().data) or []
    # 🔴 filtrado NO BANCO, em lotes de 100 (como o prever): o PostgREST do Supabase
    # corta em 1000 linhas por padrao, e ler tudo para filtrar aqui perderia linhas.
    selos = _em_lotes("fid_selos", "id,telefone,cartao,posicao,indicacao_id,area,"
                      "escolhido_em,agendado_em", "telefone", sorted(p["telefone"] for p in part))
    avisos = _em_lotes("fid_avisos", "id,indicacao_id,tipo,wamid,erro_codigo,reservado_em",
                       "indicacao_id", sorted(i["id"] for i in ind))
    areas = {a["codigo"]: a["titulo"]
             for a in (sb.table("fid_areas").select("codigo,titulo").execute().data or [])}
    return ind, part, selos, avisos, areas


def _confirmar_aviso(aviso_id, wamid):
    """fid_confirmar_aviso e idempotente com o MESMO wamid: repetir e seguro."""
    erro = None
    for tentativa in range(3):
        try:
            d = _sb().rpc("fid_confirmar_aviso",
                          {"p_aviso": aviso_id, "p_wamid": wamid}).execute().data
            if isinstance(d, dict) and d.get("ok"):
                return True, None
            return False, f"o banco recusou: {d}"
        except Exception as e:
            erro = str(e)[:300]
            time.sleep(1.5 * (tentativa + 1))
    return False, erro


def _decidir(ind_id, decisao):
    """Decide no banco (selo + carimbo do aviso, 1 transacao) -> manda o template ->
    confirma. -> (tipo_de_mensagem, texto). O banco e o juiz: decidir de novo a mesma
    coisa devolve mudou=false e NAO manda nada."""
    try:
        r = _sb().rpc("fid_decidir_indicacao", {"p_id": ind_id, "p_decisao": decisao,
                                                "p_por": "painel"}).execute().data
    except Exception as e:
        return "error", (f"❌ O banco não respondeu ({str(e)[:150]}). **Recarregue a página "
                         f"antes de tentar de novo**: se a indicação aparecer marcada, o aviso "
                         f"não saiu e vai para o Relatório.")
    if not (isinstance(r, dict) and r.get("ok")):
        motivo = r.get("motivo") if isinstance(r, dict) else None
        return "error", "❌ " + MOTIVO_RECUSA.get(motivo, f"O banco recusou: {r}")
    if not r.get("mudou"):
        return "info", "Nada mudou: a indicação já estava assim."

    quem = r["indicante"]
    tpl = TEMPLATE_FECHOU if decisao == "FECHOU" else TEMPLATE_NAO_FECHOU
    wamid, err = enviar_template(quem["telefone"], quem["nome"], tpl)
    selo = r.get("selo") or {}
    o_que = (f"selo {selo.get('posicao')}/10 para **{quem['nome']}**" if decisao == "FECHOU"
             else f"aviso para **{quem['nome']}**")
    if wamid:
        ok, msg = _confirmar_aviso(r["aviso_id"], wamid)
        if ok:
            return "success", (f"✅ **{r['indicada']}**: {ROTULO_STATUS[decisao]} · {o_que} · "
                               f"mensagem enviada.")
        return "error", (f"🔴 A mensagem SAIU para {quem['nome']}, mas o banco não gravou "
                         f"({msg}). **NÃO marque de novo.** Correção (repetir é seguro): "
                         f"`select fid_confirmar_aviso({r['aviso_id']}, '{wamid}');`")
    codigo, mensagem, talvez = err
    cod = ("TALVEZ_SAIU:" if talvez else "NAO_SAIU:") + codigo
    try:
        _sb().rpc("fid_registrar_erro_aviso", {"p_aviso": r["aviso_id"], "p_codigo": cod,
                                               "p_mensagem": mensagem}).execute()
    except Exception as e2:
        mensagem += f" [e o erro NÃO foi gravado no banco: {str(e2)[:150]}]"
    return "error", (f"⚠️ **{r['indicada']}** ficou {ROTULO_STATUS[decisao]} ({o_que}), mas a "
                     f"mensagem **não saiu** ({cod}: {mensagem[:200]}). Está no Relatório. "
                     f"Avisar a cliente pelo WhatsApp da unidade.")


def _render_indicacoes(unidade):
    # mesma regra do _render: TODA chave prefixada pela unidade
    k = f"fid_ind_{unidade}_"
    rotulo = UNIDADE_ROTULO[unidade]
    st.markdown(f"## 🎁 Indicações — {rotulo}")
    st.caption(f"Cartão Fidelidade · {VERSAO_ABA}")

    aviso = st.session_state.pop(k + "msg", None)
    if aviso:
        getattr(st, aviso[0])(aviso[1])

    try:
        cfg, manut = _estado(unidade)
    except Exception as e:
        st.error(f"⚠️ Não consegui ler a configuração do FID: {e}")
        return
    pode = bool(cfg and cfg.get("ativo") and not manut)
    if not pode:
        st.warning("🔴 FID desligado ou em manutenção — dá para ver tudo, mas marcar "
                   "FECHOU / NÃO FECHOU (que manda mensagem) fica bloqueado.")

    try:
        ind, part, selos, avisos, areas = _ler_indicacoes(unidade)
    except Exception as e:
        st.error(f"⚠️ Não consegui ler as indicações: {e}")
        return

    nomes = {p["telefone"]: p["nome"] for p in part}
    indicantes = {i["indicante"] for i in ind}
    novas = [i for i in ind if i["status"] == "NOVA"]
    a_agendar = [x for x in selos if x.get("area") and not x.get("agendado_em")]
    esperando = [x for x in selos if not x.get("area")]
    participantes = [p for p in part if p["status"] == "ENVIADO" or p["telefone"] in indicantes]

    m1, m2, m3 = st.columns(3)
    m1.metric("👥 Participantes", len(participantes))
    m2.metric("🆕 Indicações a tratar", len(novas))
    m3.metric("🎁 Sessões a agendar", len(a_agendar))

    link = st.column_config.LinkColumn("WhatsApp", display_text="abrir")

    # ---- indicacoes novas
    st.markdown("### 🆕 Indicações a tratar")
    if novas:
        st.dataframe(pd.DataFrame([{
            "Indicada": i["nome"],
            "Telefone": "+" + i["telefone"],
            "WhatsApp": f"https://wa.me/{i['telefone']}",
            "Tem WhatsApp?": "sim" if i.get("tem_whatsapp") else "não (ligar)",
            "Quem indicou": nomes.get(i["indicante"], i["indicante"]),
            "Recebida": _hora_sp(i.get("criado_em")),
            "Obs.": "⚠️ já está no programa" if i.get("ja_participante") else "",
        } for i in novas]), use_container_width=True, hide_index=True,
            column_config={"WhatsApp": link})
    else:
        st.success("Nenhuma indicação nova.")

    # ---- decidir (NOVA, ou NAO_FECHOU que depois fechou - decisao B de 24/09)
    st.markdown("### ✍️ Marcar se fechou")
    decidiveis = sorted([i for i in ind if i["status"] in ("NOVA", "NAO_FECHOU")],
                        key=lambda i: (i["status"] != "NOVA", str(i.get("criado_em"))))
    if not decidiveis:
        st.caption("Nada para marcar.")
    else:
        op = {i["id"]: (f"{i['nome']} · +{i['telefone']} — indicada por "
                        f"{nomes.get(i['indicante'], i['indicante'])} · {ROTULO_STATUS[i['status']]}")
              for i in decidiveis}
        # a escolhida anterior pode ter saido da lista (foi marcada): volta pra 1a
        if st.session_state.get(k + "sel") not in op:
            st.session_state.pop(k + "sel", None)
        sel = st.selectbox("Indicação", list(op), format_func=op.get, key=k + "sel")
        atual = next(i for i in decidiveis if i["id"] == sel)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Fechou plano", disabled=not pode, use_container_width=True,
                         key=k + "btn_fechou"):
                st.session_state[k + "conf"] = (sel, "FECHOU")
                st.rerun()
        with c2:
            if st.button("❌ Não fechou", disabled=(not pode or atual["status"] == "NAO_FECHOU"),
                         use_container_width=True, key=k + "btn_nao"):
                st.session_state[k + "conf"] = (sel, "NAO_FECHOU")
                st.rerun()

        conf = st.session_state.get(k + "conf")
        if conf and conf[0] == sel and pode:
            quem = nomes.get(atual["indicante"], atual["indicante"])
            if conf[1] == "FECHOU":
                st.warning(f"⚠️ Confirmar: **{atual['nome']}** FECHOU plano? **{quem}** ganha "
                           f"1 selo e recebe a mensagem para escolher a área. **Não dá para "
                           f"desfazer.**")
            else:
                st.warning(f"⚠️ Confirmar: **{atual['nome']}** NÃO fechou? **{quem}** recebe a "
                           f"mensagem de que a indicação não fechou. Se fechar depois, é só "
                           f"marcar FECHOU aqui.")
            s1, s2 = st.columns(2)
            with s1:
                if st.button("✅ Sim, confirmar", type="primary", use_container_width=True,
                             key=k + "btn_sim"):
                    if st.session_state.get(k + "em_andamento"):
                        st.warning("⚠️ Já está enviando. Aguarde.")
                    else:
                        st.session_state[k + "em_andamento"] = True
                        try:
                            st.session_state[k + "msg"] = _decidir(conf[0], conf[1])
                        finally:
                            st.session_state[k + "em_andamento"] = False
                            st.session_state.pop(k + "conf", None)
                        st.rerun()
            with s2:
                if st.button("Cancelar", use_container_width=True, key=k + "btn_cancela"):
                    st.session_state.pop(k + "conf", None)
                    st.rerun()

    # ---- sessoes do premio
    st.markdown("### 🎁 Sessões a agendar")
    if a_agendar:
        st.dataframe(pd.DataFrame([{
            "Cliente": nomes.get(x["telefone"], x["telefone"]),
            "Telefone": "+" + x["telefone"],
            "WhatsApp": f"https://wa.me/{x['telefone']}",
            "Sessão de": areas.get(x["area"], x["area"]),
            "Selo": f"{x['posicao']}/10" + (f" (cartão {x['cartao']})" if x["cartao"] > 1 else ""),
            "Escolhida": _hora_sp(x.get("escolhido_em")),
        } for x in a_agendar]), use_container_width=True, hide_index=True,
            column_config={"WhatsApp": link})
        op_s = {x["id"]: f"{nomes.get(x['telefone'], x['telefone'])} — "
                         f"{areas.get(x['area'], x['area'])} (selo {x['posicao']}/10)"
                for x in a_agendar}
        if st.session_state.get(k + "sel_s") not in op_s:
            st.session_state.pop(k + "sel_s", None)
        sel_s = st.selectbox("Sessão", list(op_s), format_func=op_s.get, key=k + "sel_s")
        if st.button("📅 Marcar como agendada", key=k + "btn_agendar"):
            try:
                r = _sb().rpc("fid_marcar_agendado", {"p_selo": sel_s, "p_por": "painel"}).execute().data
            except Exception as e:
                r = {"ok": False, "erro": str(e)[:200]}
            st.session_state[k + "msg"] = (("success", "✅ Marcada como agendada.")
                                           if isinstance(r, dict) and r.get("ok")
                                           else ("error", f"❌ Não marquei: {r}"))
            st.rerun()
    else:
        st.info("Nenhuma sessão esperando agendamento.")
    if esperando:
        st.caption(f"⏳ {len(esperando)} selo(s) esperando a cliente escolher a área.")

    # ---- participantes
    st.markdown("### 👥 Participantes")
    if participantes:
        linhas = []
        for p in participantes:
            meus = [x for x in selos if x["telefone"] == p["telefone"]]
            cartao = max([x["cartao"] for x in meus], default=1)
            minhas = [i for i in ind if i["indicante"] == p["telefone"]]
            linhas.append({
                "Cliente": p["nome"],
                "Telefone": "+" + p["telefone"],
                "Selos": f"{sum(1 for x in meus if x['cartao'] == cartao)}/10",
                "Cartão": cartao,
                "Indicou": len(minhas),
                "Fecharam": sum(1 for i in minhas if i["status"] == "FECHOU"),
                "Não fecharam": sum(1 for i in minhas if i["status"] == "NAO_FECHOU"),
                "Novas": sum(1 for i in minhas if i["status"] == "NOVA"),
                "Boas-vindas": _hora_sp(p.get("enviado_em")),
            })
        df = pd.DataFrame(linhas).sort_values(["Indicou", "Cliente"], ascending=[False, True])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("Ninguém no programa ainda.")

    # ---- historico
    if ind:
        ultimo = {}
        for a in avisos:                      # ordenado por id: o ultimo vence
            ultimo[a["indicacao_id"]] = a
        def _aviso(i):
            a = ultimo.get(i["id"])
            if not a:
                return "—"
            if a.get("wamid"):
                return "✅ enviado"
            if a.get("erro_codigo"):
                return "❌ " + a["erro_codigo"]
            return "⏳ sem confirmação"
        with st.expander(f"📜 Todas as indicações ({len(ind)})"):
            st.dataframe(pd.DataFrame([{
                "Indicada": i["nome"],
                "Telefone": "+" + i["telefone"],
                "Quem indicou": nomes.get(i["indicante"], i["indicante"]),
                "Status": ROTULO_STATUS.get(i["status"], i["status"]),
                "Recebida": _hora_sp(i.get("criado_em")),
                "Marcada": _hora_sp(i.get("decidido_em")),
                "Aviso": _aviso(i),
            } for i in ind]), use_container_width=True, hide_index=True)


def _render_unidade(unidade):
    """FID-10: sub-abas. "Disparo" e o _render de sempre, sem mudanca."""
    t_disp, t_ind = st.tabs(["📤 Disparo", "🎁 Indicações"])
    with t_disp:
        _render(unidade)
    with t_ind:
        _render_indicacoes(unidade)


def render_aba_fid_mogi():
    _render_unidade("MOGI")


def render_aba_fid_suzano():
    _render_unidade("SUZANO")
