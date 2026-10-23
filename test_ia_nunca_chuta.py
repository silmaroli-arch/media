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

    contextos.clear()
    with patch("app.ia_preparo._tentar_provedor", side_effect=tentar), \
            patch("app.ia_preparo._cliente_anthropic", return_value=None), \
            patch("app.medicamentos_anvisa.medicamentos_citados", side_effect=RuntimeError("banco fora")):
        r = _responder_com_duas_ias("Posso tomar Poviztra?", exame)
    checar("Falha na lista da ANVISA não derruba a resposta", r["final"] and "ANVISA" not in contextos[0])

print("\nTodos os testes de 'a IA nunca chuta' passaram.")
