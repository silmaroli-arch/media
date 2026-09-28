"""Testa a checagem por dicionário de português em app.whatsapp_conversa
(pedido do Silvan, 2026-09-24): "se pelo menos duas palavras não
existirem na frase, dê erro". Diferente de `_eh_mensagem_sem_sentido_
minimo` (só olha proporção/repetição de caracteres, ver
test_whatsapp_mensagem_sem_sentido.py), esta usa o pacote
`pyspellchecker` (dicionário de português embutido no próprio pacote,
ver requirements.txt) - pega palavras inventadas/digitação aleatória que
ainda têm "cara de texto" o suficiente pra passar pela checagem acima
(ex.: "asdkjf qwerty lorem").

Importante: o `pyspellchecker` é uma dependência NOVA, adicionada nesta
mesma sessão - ainda não está instalada neste ambiente de
desenvolvimento nem na máquina do Silvan (só vai estar disponível de
verdade depois do próximo deploy no Render, que instala via `pip` com
internet normal - ver HANDOFF_CHAT.md). Por isso estes testes NÃO
dependem do pacote estar de fato instalado:

- A Parte 1 usa `unittest.mock.patch` em `_obter_verificador_ortografico`
  pra simular um dicionário fake (um objeto qualquer com um método
  `.unknown(palavras)` - mesmo contrato do SpellChecker de verdade) -
  testa a lógica de contagem/limite de `_eh_mensagem_com_muitas_
  palavras_desconhecidas` sem precisar do pacote real.
- A Parte 2 confirma o comportamento ATUAL, sem o pacote instalado (o
  caso real deste ambiente agora): a função deve devolver False (não
  afeta nada) e `processar_mensagem` deve seguir o fluxo de sempre -
  garante que a ausência do pacote não trava nem muda nenhum
  comportamento existente.
- A Parte 3 mocka `_eh_mensagem_com_muitas_palavras_desconhecidas`
  diretamente (via `app.whatsapp_conversa`) pra testar, com banco de
  verdade, que quando ela SINALIZA True (simulando o pacote já
  instalado e reconhecendo 2+ palavras desconhecidas), `processar_
  mensagem` devolve o mesmo aviso de sempre, sem criar PerguntaPendente/
  ChatMensagem - preparando o terreno pra quando o pacote estiver
  instalado de verdade, sem precisar reescrever o teste depois."""
from unittest.mock import patch

from app.whatsapp_conversa import (
    _eh_mensagem_com_muitas_palavras_desconhecidas,
    _obter_verificador_ortografico,
)


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


class _VerificadorFake:
    """Simula o mesmo contrato do SpellChecker de verdade
    (`.unknown(lista_de_palavras)` devolve o subconjunto desconhecido) -
    aqui, tudo que não estiver em `_conhecidas` é "desconhecido"."""

    def __init__(self, conhecidas):
        self._conhecidas = set(conhecidas)

    def unknown(self, palavras):
        return {p for p in palavras if p not in self._conhecidas}


# --- Parte 1: lógica de contagem/limite, com dicionário fake mockado ---

_CONHECIDAS = {"posso", "comer", "batata", "frita", "exame", "jejum", "agua", "durante", "beber"}

with patch(
    "app.whatsapp_conversa._obter_verificador_ortografico",
    return_value=_VerificadorFake(_CONHECIDAS),
):
    checar(
        "Duas palavras desconhecidas (>= 3 letras cada) é sem sentido",
        _eh_mensagem_com_muitas_palavras_desconhecidas("asdkjf qwerty lorem"),
    )
    checar(
        "Uma palavra só desconhecida NÃO é o suficiente (pedido literal: pelo menos DUAS)",
        not _eh_mensagem_com_muitas_palavras_desconhecidas("Posso comer asdkjf?"),
    )
    checar(
        "Frase toda com palavras conhecidas NÃO é sem sentido",
        not _eh_mensagem_com_muitas_palavras_desconhecidas("Posso beber agua durante o jejum?"),
    )
    checar(
        "Palavras curtas (< 3 letras) não contam pra essa checagem (abreviação comum não é penalizada)",
        not _eh_mensagem_com_muitas_palavras_desconhecidas("vc pq tb"),
    )
    checar(
        "Mensagem vazia não quebra a função",
        not _eh_mensagem_com_muitas_palavras_desconhecidas(""),
    )
    checar(
        "None não quebra a função",
        not _eh_mensagem_com_muitas_palavras_desconhecidas(None),
    )


# --- Parte 2: comportamento ATUAL deste ambiente (pacote ainda não
# instalado) - garante que a ausência do pyspellchecker não afeta nada. ---

verificador_real = _obter_verificador_ortografico()
if verificador_real is None:
    checar(
        "Sem o pacote instalado (caso real deste ambiente agora), a checagem nunca marca nada",
        not _eh_mensagem_com_muitas_palavras_desconhecidas("asdkjf qwerty lorem completamente inventado"),
    )
    print("(pyspellchecker não está instalado neste ambiente - checagem confirmada como no-op silencioso, como esperado)")
else:
    print("(pyspellchecker JÁ está instalado neste ambiente - Parte 2 pulada, use a Parte 1/3 pra validar o comportamento)")


# --- Parte 3: via processar_mensagem, com banco - mockando o sinal
# direto (não depende do pacote real estar instalado) ---
from app import create_app, db
from app.models import ChatMensagem, ConversaWhatsapp, Paciente, PerguntaPendente
from app.whatsapp_conversa import MENSAGEM_MENSAGEM_SEM_SENTIDO, processar_mensagem

app = create_app()

with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    telefone = "+5527900005432"

    ConversaWhatsapp.query.filter_by(telefone=telefone).delete()
    db.session.commit()

    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")  # identifica o João (exame único: colonoscopia)

    perguntas_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    mensagens_antes = ChatMensagem.query.filter_by(paciente_id=joao.id).count()

    with patch("app.whatsapp_conversa._eh_mensagem_com_muitas_palavras_desconhecidas", return_value=True):
        resposta = processar_mensagem(telefone, "asdkjf qwerty lorem completamente inventado")

    checar(
        "Sinal de 'muitas palavras desconhecidas' devolve o mesmo aviso de sempre",
        resposta == MENSAGEM_MENSAGEM_SEM_SENTIDO,
    )
    checar(
        "NÃO cria PerguntaPendente",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes,
    )
    checar(
        "NÃO cria ChatMensagem",
        ChatMensagem.query.filter_by(paciente_id=joao.id).count() == mensagens_antes,
    )

    # Depois do aviso, uma pergunta de verdade continua funcionando
    # normalmente, na mesma conversa (sem o mock - comportamento real).
    resposta = processar_mensagem(telefone, "Posso beber agua durante o jejum?")
    checar(
        "Pergunta de verdade depois do aviso funciona normalmente (bate com a FAQ do seed.py)",
        "água pura é permitida" in resposta,
    )

print("\nTodos os testes da checagem por dicionário de português passaram.")
