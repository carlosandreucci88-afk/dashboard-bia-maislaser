import streamlit as st
import pandas as pd
import requests
import json
import time
import re
from supabase import create_client

# ============================================================
# CONFIGURAÇÕES DO MÓDULO
# ============================================================
# Templates aprovados na Meta — parte da lógica de negócio, não credencial.
NOME_MODELO_MENSAGEM        = "confirmacao_agenda_maislaser_v4"
NOME_MODELO_MENSAGEM_2SESS  = "confirmacao_agenda_maislaser_2sessoes_v2"

# Credenciais e URLs sensíveis: lidos de st.secrets dentro das funções.
# Necessário configurar no Streamlit Cloud (share.streamlit.io/settings/secrets):
#   TOKEN_META            — token long-lived do WhatsApp Business
#   ID_TELEFONE_META      — phone number ID da Meta
#   URL_WEBHOOK_CONTEXTO  — Apps Script /exec do robô confirmação
#   NUMERO_ROBO_ALERTAS   — número que recebe os alertas (ex: "5511911177883")

# ============================================================
# FUNÇÃO: Limpar número de telefone
# ============================================================
def limpar_numero(numero):
    """
    Limpa o número deixando apenas dígitos e garante o formato
    correto internacional com DDI 55 (Brasil).
    Trata casos onde o pandas lê como float (ex: 5511999990000.0)
    """
    if pd.isna(numero):
        return None

    num_str = str(numero).strip()

    # Remove ponto flutuante gerado pelo pandas (ex: 55119999.0 → 55119999)
    if num_str.endswith('.0'):
        num_str = num_str[:-2]

    # Remove tudo que não for dígito
    num_limpo = re.sub(r'\D', '', num_str)

    if num_limpo == '':
        return None

    # ✅ CORREÇÃO: Evita duplicar o 55 — verifica se JÁ começa com 55
    # e se o comprimento faz sentido para um número brasileiro com DDI
    # Número brasileiro com DDI: 55 + DDD (2) + número (8 ou 9) = 12 ou 13 dígitos
    if num_limpo.startswith('55') and len(num_limpo) >= 12:
        return num_limpo  # Já está correto, não adiciona 55 novamente
    elif not num_limpo.startswith('55'):
        return '55' + num_limpo
    else:
        return num_limpo

# ============================================================
# FUNÇÃO: Limpar nome do serviço para exibição na mensagem
# ============================================================
def limpar_nome_servico(servico):
    """
    Remove os prefixos 'F - ' e 'M - ' que o sistema UNO adiciona
    nos serviços (F = Feminino, M = Masculino).
    Deixa o nome mais limpo e natural na mensagem do cliente.
    Ex: 'F - Depilação de Axilas cortesia' → 'Depilação de Axilas'
    Também remove sufixos como 'cortesia', '(área P)', etc.
    """
    if pd.isna(servico) or str(servico).strip() == '':
        return ''

    s = str(servico).strip()

    # Remove prefixo de gênero 'F - ' ou 'M - '
    s = re.sub(r'^[FM]\s*-\s*', '', s)

    # Remove indicadores de área como '(área P)', '(área M)', '(área G)'
    s = re.sub(r'\(área\s*[A-Z]\)', '', s, flags=re.IGNORECASE)

    # Remove a palavra 'cortesia' no final (case insensitive)
    s = re.sub(r'\bcortesia\b', '', s, flags=re.IGNORECASE)

    # Remove espaços duplos e trim
    s = re.sub(r'\s+', ' ', s).strip()

    # Remove vírgula ou hífen no final se sobrar
    s = s.rstrip(',-').strip()

    return s

# ============================================================
# FUNÇÃO: Enviar mensagem via WhatsApp Cloud API
# ============================================================
def enviar_mensagem_whatsapp(nome, horario, procedimento, unidade, telefone_destino):
    """
    Faz a chamada de API para a Meta enviando o modelo estruturado
    na versão nativa v25.0.
    Template v2: {{1}} Nome, {{2}} Horário, {{3}} Serviços, {{4}} Unidade
    """
    url = f"https://graph.facebook.com/v25.0/{st.secrets['ID_TELEFONE_META']}/messages"

    headers = {
        "Authorization": f"Bearer {st.secrets['TOKEN_META']}",
        "Content-Type": "application/json"
    }

    # Garante que quebras de linha não quebrem a API
    procedimento_limpo = str(procedimento).replace('\n', ' ').replace('\r', '').strip()

    payload = {
        "messaging_product": "whatsapp",
        "to": str(telefone_destino),
        "type": "template",
        "template": {
            "name": NOME_MODELO_MENSAGEM,
            "language": {
                "code": "pt_BR"
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(nome)},           # {{1}} Nome do cliente
                        {"type": "text", "text": str(horario)},        # {{2}} Data e horário
                        {"type": "text", "text": procedimento_limpo},  # {{3}} Serviço(s) agrupados
                        {"type": "text", "text": str(unidade)}         # {{4}} Nome da unidade
                    ]
                }
            ]
        }
    }

    try:
        resposta = requests.post(url, headers=headers, json=payload)
        return resposta.status_code, resposta.json()
    except Exception as e:
        return 500, {"error": str(e)}

# ============================================================
# FUNÇÃO: Enviar mensagem de 2 sessões via WhatsApp Cloud API
# ============================================================
def enviar_mensagem_2sessoes(nome, horario1, servico1, horario2, servico2, unidade, telefone_destino):
    """
    Dispara o template especial para clientes com 2 sessões no mesmo dia.
    Template: {{1}} Nome, {{2}} Horário1, {{3}} Serviço1, {{4}} Horário2, {{5}} Serviço2, {{6}} Unidade
    """
    url = f"https://graph.facebook.com/v25.0/{st.secrets['ID_TELEFONE_META']}/messages"
    headers = {
        "Authorization": f"Bearer {st.secrets['TOKEN_META']}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": str(telefone_destino),
        "type": "template",
        "template": {
            "name": NOME_MODELO_MENSAGEM_2SESS,
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(nome)},
                        {"type": "text", "text": str(horario1)},
                        {"type": "text", "text": str(servico1).replace('\n', ' ').strip()},
                        {"type": "text", "text": str(horario2)},
                        {"type": "text", "text": str(servico2).replace('\n', ' ').strip()},
                        {"type": "text", "text": str(unidade)}
                    ]
                }
            ]
        }
    }
    try:
        resposta = requests.post(url, headers=headers, json=payload)
        return resposta.status_code, resposta.json()
    except Exception as e:
        return 500, {"error": str(e)}


# ============================================================
# ENTRY POINT — chamado pelo dashboard_maislaser.py dentro da tab Disparar agenda
# ============================================================
def render_aba_disparador():
    """
    Renderiza a aba 🚀 Disparar agenda do dashboard centralizado.
    Portado do app.py do robo-maislaser. Mantém UI/UX e regras de negócio
    intactas; só foi adaptado pra:
      - rodar dentro de uma aba (sem set_page_config / title de página)
      - ler credenciais de st.secrets
      - usar 'return' no lugar de st.stop() (evita parar o dashboard inteiro)
      - prefixar chaves de session_state/botões com 'disp_' (evita colisão)
    """
    # ============================================================
    # INTERFACE — SELEÇÃO DE UNIDADE E NÚMERO DE ALERTA
    # ============================================================
    unidade_selecionada = st.selectbox(
        "Selecione a Unidade que está operando hoje:",
        ["", "Mogi das Cruzes", "Suzano"],
        index=0
    )

    if not unidade_selecionada:
        st.info("👆 Selecione a unidade acima para continuar.")
        return

    # Confirmação da unidade
    st.warning(f"⚠️ Você selecionou a unidade **{unidade_selecionada}** — está correto?")
    col1, col2 = st.columns(2)
    with col1:
        confirmar_unidade = st.button(f"✅ Sim, é {unidade_selecionada}", use_container_width=True, key="disp_btn_confirmar_unidade")
    with col2:
        corrigir_unidade = st.button("❌ Não, corrigir", use_container_width=True, key="disp_btn_corrigir_unidade")

    if corrigir_unidade:
        st.error("⬆️ Por favor, corrija a unidade no campo acima antes de continuar.")
        return

    if not confirmar_unidade and not st.session_state.get("disp_unidade_confirmada"):
        return

    if confirmar_unidade:
        st.session_state["disp_unidade_confirmada"] = True
        st.session_state["disp_unidade_valor"] = unidade_selecionada

    # Garante que a unidade confirmada seja usada
    if st.session_state.get("disp_unidade_confirmada"):
        unidade_selecionada = st.session_state.get("disp_unidade_valor", unidade_selecionada)
        st.success(f"✅ Unidade **{unidade_selecionada}** confirmada!")

    numero_alerta_input = st.text_input(
        "Digite o número de WhatsApp que receberá os alertas (com DDD):",
        value=""
    )

    if numero_alerta_input:
        numero_alerta_formatado = limpar_numero(numero_alerta_input)
        st.info(f"📢 Os alertas serão enviados para: {numero_alerta_formatado}")

        # Botão verde para ativar alertas
        numero_robo = st.secrets["NUMERO_ROBO_ALERTAS"]
        link_whatsapp = f"https://wa.me/{numero_robo}?text=oi"
        st.markdown(
            f"""
            <a href="{link_whatsapp}" target="_blank" style="
                display: inline-block;
                background-color: #25D366;
                color: white;
                padding: 10px 20px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: bold;
                font-size: 15px;
                margin-top: 4px;
            ">
            📲 Clique aqui para ativar os alertas no seu WhatsApp
            </a>
            <p style="font-size: 12px; color: gray; margin-top: 6px;">
            ⚠️ Envie o "oi" antes de iniciar os disparos para receber os alertas.
            </p>
            """,
            unsafe_allow_html=True
        )

        # Confirmação de que enviou o oi
        alertas_ativados = st.checkbox("✅ Já enviei o 'oi' e estou pronto para disparar!", key="disp_alertas_ativados")
        if not alertas_ativados:
            return

    # ============================================================
    # UPLOAD DA PLANILHA
    # ============================================================
    arquivo_upload = st.file_uploader("Selecione a planilha do UNO (.xlsx)", type=["xlsx"])

    if arquivo_upload is not None:
        try:
            df = pd.read_excel(arquivo_upload)
            total_original = len(df)

            # Colunas obrigatórias que o sistema UNO exporta
            # Colunas essenciais para o disparo funcionar
            colunas_essenciais = ['Data', 'Cliente', 'Telefone', 'Serviço']
            colunas_encontradas = df.columns.tolist()

            # --------------------------------------------------
            # VALIDAÇÃO VISUAL DAS COLUNAS
            # --------------------------------------------------
            st.subheader("🔍 Validação da Planilha")

            linhas_html = ""
            todas_ok = True
            for col in colunas_essenciais:
                if col in colunas_encontradas:
                    status_icon  = "✅"
                    status_texto = "Encontrada"
                    cor_bg       = "#e8f5e9"
                    cor_txt      = "#1b5e20"
                else:
                    status_icon  = "❌"
                    status_texto = "NÃO ENCONTRADA"
                    cor_bg       = "#ffebee"
                    cor_txt      = "#b71c1c"
                    todas_ok = False
                linhas_html += f"""
                <tr style="background:{cor_bg}">
                    <td style="padding:8px 14px;font-weight:bold;color:{cor_txt}">{status_icon} {col}</td>
                    <td style="padding:8px 14px;color:{cor_txt}">{status_texto}</td>
                </tr>"""

            st.markdown(f"""
            <table style="width:100%;border-collapse:collapse;border-radius:8px;overflow:hidden;font-size:15px;margin-bottom:12px">
                <thead>
                    <tr style="background:#1565c0;color:white">
                        <th style="padding:10px 14px;text-align:left">Coluna</th>
                        <th style="padding:10px 14px;text-align:left">Status</th>
                    </tr>
                </thead>
                <tbody>{linhas_html}</tbody>
            </table>
            """, unsafe_allow_html=True)

            # Colunas extras encontradas (além das essenciais)
            extras = [c for c in colunas_encontradas if c not in colunas_essenciais]
            if extras:
                st.info(f"ℹ️ Colunas extras encontradas (ignoradas): {', '.join(extras)}")

            if not todas_ok:
                st.error("🚫 **Disparo bloqueado!** Corrija a planilha antes de continuar — uma ou mais colunas essenciais estão ausentes.")
                return

            st.success("✅ Todas as colunas essenciais foram encontradas! Planilha válida.")

            colunas_necessarias = ['Cliente', 'Serviço', 'Telefone']
            verificacao_colunas = True

            if True:
                # --------------------------------------------------
                # ✅ CORREÇÃO 1: Filtrar apenas agendamentos ATIVOS
                # Ignora registros Cancelados, Faltou, Remarcado etc.
                # --------------------------------------------------
                if 'Situação' in df.columns:
                    total_antes_filtro = len(df)
                    df = df[df['Situação'].str.strip().str.lower() == 'agendado']
                    total_filtrados = total_antes_filtro - len(df)
                    if total_filtrados > 0:
                        st.warning(
                            f"⚠️ {total_filtrados} registro(s) ignorado(s) por não estarem "
                            f"com situação 'Agendado' (cancelados, faltou, etc.)."
                        )

                # --------------------------------------------------
                # LIMPEZA DOS DADOS
                # --------------------------------------------------
                df['Cliente'] = df['Cliente'].fillna('').astype(str).str.strip()
                df['Telefone'] = df['Telefone'].apply(limpar_numero).fillna('').astype(str)

                # ✅ CORREÇÃO 2: Limpa prefixos F-/M- e sufixos de área dos serviços
                df['Serviço'] = df['Serviço'].apply(limpar_nome_servico)

                # Converte data serial do Excel para horário legível
                df['Horario'] = pd.to_datetime(df['Data']).dt.strftime('%d/%m/%Y às %Hh%M')

                # Remove linhas onde o serviço ficou vazio após limpeza
                df = df[df['Serviço'] != '']

                # --------------------------------------------------
                # AGRUPAMENTO: Serviços agrupados POR HORÁRIO
                # Cada linha = 1 cliente com seus horários e serviços corretos
                # --------------------------------------------------

                # Agrupa serviços por Cliente + Telefone + Horario (respeita sessões diferentes)
                df_srv_horario = df.groupby(['Cliente', 'Telefone', 'Horario'])['Serviço'].apply(
                    lambda x: ', '.join(sorted(set(x)))
                ).reset_index()

                # Pega horários distintos ordenados por cliente
                df_horarios = df.groupby(['Cliente', 'Telefone'])['Horario'].apply(
                    lambda x: sorted(set(x))
                ).reset_index()
                df_horarios['Horario2'] = df_horarios['Horario'].apply(lambda x: x[1] if len(x) > 1 else "")
                df_horarios['Horario']  = df_horarios['Horario'].apply(lambda x: x[0])

                # Serviços da sessão 1 (primeiro horário)
                df_srv_h1 = df_srv_horario.groupby(['Cliente', 'Telefone']).first().reset_index()[['Cliente', 'Telefone', 'Serviço']]

                # Serviços da sessão 2 (segundo horário, se existir)
                df_srv_h2 = df_srv_horario.groupby(['Cliente', 'Telefone']).apply(
                    lambda x: x.iloc[1]['Serviço'] if len(x) > 1 else ""
                ).reset_index().rename(columns={0: 'Servico2'})

                # Monta dataframe final com serviços corretos por sessão
                df_agrupado = df_srv_h1.merge(df_horarios[['Cliente', 'Telefone', 'Horario', 'Horario2']], on=['Cliente', 'Telefone'])
                df_agrupado = df_agrupado.merge(df_srv_h2, on=['Cliente', 'Telefone'], how='left')
                df_agrupado['Servico2'] = df_agrupado['Servico2'].fillna("")
                df_agrupado = df_agrupado[['Cliente', 'Serviço', 'Telefone', 'Horario', 'Horario2', 'Servico2']]
                total_agrupado = len(df_agrupado)

                # --------------------------------------------------
                # EXIBIÇÃO DOS DADOS NA TELA
                # --------------------------------------------------
                st.success(
                    f"✅ Planilha carregada com sucesso! "
                    f"{len(df)} registros válidos encontrados."
                )
                st.info(
                    f"🔄 Agrupamento concluído: serviços unidos por cliente. "
                    f"Serão disparadas **{total_agrupado}** mensagens."
                )

                st.subheader(f"Pré-visualização dos disparos ({unidade_selecionada}):")
                st.dataframe(df_agrupado, use_container_width=True)

                # --------------------------------------------------
                # BOTÃO DE DISPARO
                # --------------------------------------------------
                if st.button("🚀 Iniciar Disparos em Massa"):
                    progresso = st.progress(0)
                    status_texto = st.empty()

                    sucessos = 0
                    erros = 0
                    falhas_contexto = 0
                    clientes_sem_contexto = []
                    total_linhas = len(df_agrupado)

                    for i, (_, linha) in enumerate(df_agrupado.iterrows()):
                        nome_cliente = linha['Cliente']
                        procedimento = linha['Serviço']
                        telefone_formatado = linha['Telefone']

                        if telefone_formatado and len(telefone_formatado) >= 12:
                            status_texto.text(
                                f"📤 Enviando {i+1}/{total_linhas}: "
                                f"{nome_cliente} ({telefone_formatado})..."
                            )

                            horario_cliente  = linha['Horario']
                            horario2_cliente = linha.get('Horario2', '')
                            servico2_cliente = linha.get('Servico2', '')

                            # Detecta se tem 2 sessões no mesmo dia
                            tem_2_sessoes = bool(horario2_cliente and horario2_cliente != horario_cliente)

                            if tem_2_sessoes:
                                code, res = enviar_mensagem_2sessoes(
                                    nome_cliente,
                                    horario_cliente, procedimento,
                                    horario2_cliente, servico2_cliente,
                                    unidade_selecionada, telefone_formatado
                                )
                            else:
                                code, res = enviar_mensagem_whatsapp(
                                    nome_cliente, horario_cliente,
                                    procedimento, unidade_selecionada,
                                    telefone_formatado
                                )

                            if code in (200, 201):
                                sucessos += 1
                                # FIX: salvar contexto com retry + timeout maior
                                # ANTES: timeout=5, except:pass (perdia contextos em lotes grandes)
                                # AGORA: timeout=15, 2 tentativas, warning se falhar
                                webhook_url = st.secrets.get("URL_WEBHOOK_CONTEXTO", "")
                                if webhook_url:
                                    ctx_payload = {
                                        "acao": "salvar_contexto",
                                        "telefone": telefone_formatado,
                                        "nome": nome_cliente,
                                        "servico": procedimento,
                                        "unidade": unidade_selecionada,
                                        "horario": horario_cliente,
                                        "horario2": horario2_cliente,
                                        "servico2": servico2_cliente,
                                        "numero_alerta": numero_alerta_formatado
                                    }
                                    ctx_ok = False
                                    for tentativa in range(2):
                                        try:
                                            r_ctx = requests.post(webhook_url, json=ctx_payload, timeout=15)
                                            if r_ctx.status_code == 200:
                                                ctx_ok = True
                                                break
                                        except requests.exceptions.Timeout:
                                            if tentativa == 0:
                                                time.sleep(2)
                                        except Exception:
                                            break
                                    if not ctx_ok:
                                        falhas_contexto += 1
                                        clientes_sem_contexto.append(nome_cliente)
                                        st.warning(
                                            f"⚠️ Contexto NÃO salvo: {nome_cliente} ({telefone_formatado}) "
                                            f"— WhatsApp enviado mas robô NÃO vai reconhecer a resposta"
                                        )
                            else:
                                erros += 1
                                st.error(
                                    f"❌ Falha — {nome_cliente} ({telefone_formatado}) | "
                                    f"HTTP {code} | {json.dumps(res, ensure_ascii=False)}"
                                )
                        else:
                            erros += 1
                            st.error(
                                f"⚠️ Número inválido ou ausente para: "
                                f"{nome_cliente} (encontrado: '{telefone_formatado}')"
                            )

                        # Delay para respeitar limites de taxa da Meta
                        time.sleep(1.5)

                        # Atualiza barra de progresso
                        progresso.progress((i + 1) / total_linhas)

                    status_texto.text("✅ Processamento concluído!")
                    if sucessos > 0 and falhas_contexto == 0:
                        st.balloons()
                    st.success(
                        f"🎉 Disparos finalizados! "
                        f"✅ Sucessos: {sucessos} | ❌ Erros/Falhas: {erros}"
                    )
                    if falhas_contexto > 0:
                        st.error(
                            f"🚨 **ATENÇÃO: {falhas_contexto} cliente(s) receberam o WhatsApp "
                            f"mas o contexto NÃO foi salvo na planilha!**\n\n"
                            f"O robô NÃO vai reconhecer as respostas dessas clientes.\n\n"
                            f"**Clientes afetados:** {', '.join(clientes_sem_contexto)}\n\n"
                            f"💡 Solução: re-dispare só para essas clientes."
                        )

                    # ─── Grava registro no histórico de disparos ───
                    try:
                        _sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                        # Extrai data das sessões (primeiro horário encontrado)
                        _data_sessoes = ""
                        if not df_agrupado.empty and "Horario" in df_agrupado.columns:
                            _primeiro = str(df_agrupado.iloc[0]["Horario"])
                            _partes = _primeiro.split(" às ")
                            if _partes:
                                _data_sessoes = _partes[0]

                        _sb.table("disparos_historico").insert({
                            "unidade": unidade_selecionada,
                            "arquivo": arquivo_upload.name if arquivo_upload else "—",
                            "data_sessoes": _data_sessoes,
                            "total_clientes": total_linhas,
                            "whatsapp_ok": sucessos,
                            "contexto_ok": sucessos - falhas_contexto,
                            "falhas_contexto": falhas_contexto,
                            "clientes_falha": ", ".join(clientes_sem_contexto) if clientes_sem_contexto else None,
                            "erros_envio": erros,
                            "numero_alerta": numero_alerta_formatado,
                        }).execute()
                    except Exception as e_hist:
                        st.caption(f"⚠️ Não salvou no histórico: {e_hist}")

        except Exception as erro_geral:
            st.error(f"❌ Erro ao processar o arquivo: {erro_geral}")
