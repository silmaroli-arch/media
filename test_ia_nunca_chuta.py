"""A IA nunca chuta (regra do Silvan, 2026-10-23): "Poviztra" (semaglutida,
nome novo) virou varfarina na resposta das IAs. Agora o sistema procura os
nomes citados na lista da ANVISA e manda "Poviztra = semaglutida" no
contexto das IAs; e, para um nome que a IA não reconhece com certeza, ela
responde que não reconheceu (MARCADOR_NAO_RECONHECIDO), sempre com revisão
do médico."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.ia_preparo import (
    MARCADOR_NAO_RECONHECIDO, PROMPT_SISTEMA, PROMPT_SISTEMA_VALIDADOR, _perguntar_claude, _responder_com_duas_ias,
)
from app.medicamentos_anvisa import contexto_medicamentos, importar_csv, medicamentos_citados
from app.models import Exame, PlataformaConfig

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def cliente(texto):
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **k: SimpleNamespace(
        content=[SimpleNamespace(text=texto)], usage=SimpleNamespace(input_tokens=10, output_tokens=2), model="claude")))


CSV = (
    "NOME_PRODUTO;CLASSE_TERAPEUTICA;SITUACAO_REGISTRO;PRINCIPIO_ATIVO\n"
    "POVIZTRA;ANTIDIABETICOS;Ativo;SEMAGLUTIDA\n"
    "MAREVAN;ANTICOAGULANTES;Ativo;VARFARINA SÓDICA\n"
    "CALMA;FITOTERAPICO;Ativo;PASSIFLORA\n"
    "AAS;ANALGESICOS;Ativo;ÁCIDO ACETILSALICÍLICO\n"
    "AAS PROTECT;ANTIAGREGANTES;Ativo;ÁCIDO ACETILSALICÍLICO\n"
).encode("latin-1")

checar("O prompt tem a regra principal de nunca chutar e o marcador",
       "NUNCA CHUTE" in PROMPT_SISTEMA and MARCADOR_NAO_RECONHECIDO in PROMPT_SISTEMA)
checar("O prompt manda usar a lista da ANVISA como fonte da verdade", "lista oficial da ANVISA" in PROMPT_SISTEMA)
checar("A validadora não troca nome de medicamento ao corrigir", "nunca trocando um nome de medicamento" in PROMPT_SISTEMA_VALIDADOR)

with app.app_context():
    checar("Sem lista importada, nada é citado", medicamentos_citados("Posso tomar Poviztra?") == [])
    importar_csv(CSV)
    citados = medicamentos_citados("Posso tomar poviztra antes do exame?")
    checar("Acha Poviztra = semaglutida na pergunta", citados == [("POVIZTRA", ["SEMAGLUTIDA"], "ANTIDIABETICOS")])
    checar("Palavra comum do dicionário ('calma') não vira medicamento",
           medicamentos_citados("Calma, posso tomar café?") == [])
    citados = medicamentos_citados("Tomo AAS Protect, preciso parar?")
    checar("Nome de duas palavras vale mais que a palavra solta", [c[0] for c in citados] == ["AAS PROTECT"])
    bloco = contexto_medicamentos("Uso Poviztra e Marevan")
    checar("Bloco do contexto lista os dois com princípio ativo e classe",
           "POVIZTRA = SEMAGLUTIDA (classe: ANTIDIABETICOS)" in bloco and "MAREVAN = VARFARINA SÓDICA" in bloco)
    checar("Pergunta sem medicamento: sem bloco", contexto_medicamentos("Posso comer arroz?") == "")

    resposta = f"{MARCADOR_NAO_RECONHECIDO}\nNão reconheci o medicamento \"Xyz\". Pode me informar o princípio ativo?"
    texto, chamada, sem_sentido, exige_revisao = _perguntar_claude(cliente(resposta), "Posso tomar Xyz?", "contexto")
    checar("'Não reconheci': marcador sai, resposta fica e exige revisão do médico",
           texto.startswith("Não reconheci") and MARCADOR_NAO_RECONHECIDO not in texto and exige_revisao)

    checar("O prompt separa 'reconhecer o que é' de 'estar no preparo' (caso do pepino)",
           "pepino" in PROMPT_SISTEMA and "NUNCA diga que não reconheceu" in PROMPT_SISTEMA)
    for escrito in ("NÃO_RECONHECIDO_REVISAR", "**nao_reconhecido_revisar**", "NAO RECONHECIDO REVISAR:"):
        texto, _, _, exige = _perguntar_claude(cliente(f"{escrito} Não reconheci o \"Xyz\"."), "Xyz?", "contexto")
        checar(f"Marcador escrito como '{escrito}' também é reconhecido e removido",
               texto == 'Não reconheci o "Xyz".' and exige == MARCADOR_NAO_RECONHECIDO)
    texto, chamada, *_ = _perguntar_claude(cliente("NÃO_SEI_ENCAMINHAR"), "x?", "contexto")
    checar("'NÃO_SEI' com acento conta como não sabe", texto is None and chamada is not None)

    from app.ia_preparo import _sintetizar_resposta
    sistemas = []
    sintese = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: sistemas.append(k["system"]) or SimpleNamespace(
        content=[SimpleNamespace(text="ok")], usage=SimpleNamespace(input_tokens=1, output_tokens=1), model="claude")))
    _sintetizar_resposta(sintese, "Posso comer pepino?", "a", "b")
    checar("Síntese não manda confirmar com a secretaria e prefere a IA que identificou",
           "NUNCA recomende ao paciente confirmar" in sistemas[0] and "use a que identificou" in sistemas[0])

    checar("Prompt: mesmo princípio ativo é o item do preparo, e nunca 'não está cadastrado'",
           "MESMO PRINCÍPIO ATIVO" in PROMPT_SISTEMA and "NUNCA escreva para o paciente que algo \"não está cadastrado\"" in PROMPT_SISTEMA
           and "diga claramente que esse medicamento específico não está cadastrado" not in PROMPT_SISTEMA)
    checar("Prompt: pergunta de sim/não começa pela resposta direta, sem 'Sim, deve ser suspenso' nem condicional",
           "PERGUNTA DE SIM OU NÃO" in PROMPT_SISTEMA and "nunca escreva \"Sim, deve ser suspenso\"" in PROMPT_SISTEMA
           and "{14 dias antes}" in PROMPT_SISTEMA)
    from app.ia_preparo import _respostas_divergem
    divergencias = []
    arbitro = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: divergencias.append(k["system"]) or SimpleNamespace(
        content=[SimpleNamespace(text="NAO")], usage=SimpleNamespace(input_tokens=1, output_tokens=1), model="claude")))
    checar("Checagem de divergência: 'Sim' contra 'Não' sempre diverge",
           _respostas_divergem(arbitro, "Não, suspenda.", "Sim, suspenda.") is True and "SEMPRE divergem" in divergencias[0])
    checar("Síntese também não diz 'não está cadastrado'", "não está cadastrado" in sistemas[0] and "Nunca escreva" in sistemas[0])
    from app.ia_preparo import _principios_anvisa
    importar_csv(CSV + "OZEMPIC;ANTIDIABETICOS;Ativo;SEMAGLUTIDA\nMOUNJARO;ANTIDIABETICOS;Ativo;TIRZEPATIDA\n".encode("latin-1"))
    checar("Medicamento do preparo ganha o princípio ativo da ANVISA no contexto",
           _principios_anvisa("Ozempic, Mounjaro, Trulicity ou similares")
           == " (princípio ativo segundo a ANVISA: SEMAGLUTIDA; TIRZEPATIDA)")
    checar("... e nada quando não acha", _principios_anvisa("Remédio para pressão") == "")

    from app.ia_preparo import _tirar_incerteza
    checar("Prompt pede tom direto, sem 'em geral'", "TOM DAS RESPOSTAS" in PROMPT_SISTEMA and "Em geral," not in PROMPT_SISTEMA)
    checar("Tom direto não vira 'não sei': o que é estabelecido é conhecimento certo (caso 'O exame dói')",
           "conhecimento CERTO" in PROMPT_SISTEMA and "se o exame dói" in PROMPT_SISTEMA)
    checar("'Em geral,' no começo das frases sai do texto",
           _tirar_incerteza("Em geral, a colonoscopia é feita com sedação. Geralmente, os gases passam rápido.")
           == "A colonoscopia é feita com sedação. Os gases passam rápido.")
    texto, *_ = _perguntar_claude(cliente("CONHECIMENTO_GERAL_REVISAR\nEm geral, chegue 30 minutos antes."), "x?", "contexto")
    checar("... também na sugestão de cada IA", texto == "Chegue 30 minutos antes.")

    config = PlataformaConfig.obter()
    config.ia_chat_provedor_1, config.ia_chat_provedor_2 = "Claude", "ChatGPT"
    db.session.commit()
    exame = Exame.query.first()
    contextos = []

    def tentar(nome, pergunta, contexto, *a, **k):
        contextos.append(contexto)
        return ("Pode manter.", MagicMock(), True, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=tentar), \
            patch("app.ia_preparo._cliente_anthropic", return_value=None):
        _responder_com_duas_ias("Posso tomar Poviztra?", exame)
    checar("As duas IAs recebem a linha da ANVISA no contexto",
           len(contextos) >= 2 and all("POVIZTRA = SEMAGLUTIDA" in c for c in contextos[:2]))

    respostas = {"Claude": ("Não reconheci o Xyz.", MagicMock(), True, False, MARCADOR_NAO_RECONHECIDO),
                 "ChatGPT": ("Em geral, evite.", MagicMock(), True, False, "CONHECIMENTO_GERAL_REVISAR")}
    with patch("app.ia_preparo._tentar_provedor", side_effect=lambda nome, *a, **k: respostas[nome]), \
            patch("app.ia_preparo._cliente_anthropic", return_value=None):
        r = _responder_com_duas_ias("Posso tomar Xyz?", exame)
    checar("Situação própria para 'não reconheceu' (não é 'conhecimento geral')",
           r["situacao"] == {"Claude": "respondeu_nao_reconhecido", "ChatGPT": "respondeu_geral"}
           and r["exige_revisao_medicamento"] is True)
    from app.ia_preparo import formatar_situacao_ias
    from app.models import PerguntaPendente
    p = PerguntaPendente(ias_situacao=formatar_situacao_ias(r))
    checar("Pergunta marca 'IA não reconheceu' para o aviso da tela", p.ia_nao_reconheceu and p.usou_conhecimento_geral)

    contextos.clear()
    with patch("app.ia_preparo._tentar_provedor", side_effect=tentar), \
            patch("app.ia_preparo._cliente_anthropic", return_value=None), \
            patch("app.medicamentos_anvisa.medicamentos_citados", side_effect=RuntimeError("banco fora")):
        r = _responder_com_duas_ias("Posso tomar Poviztra?", exame)
    checar("Falha na lista da ANVISA não derruba a resposta", r["final"] and "ANVISA" not in contextos[0])

print("\nTodos os testes de 'a IA nunca chuta' passaram.")
