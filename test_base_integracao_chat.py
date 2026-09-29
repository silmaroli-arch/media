"""Testa a INTEGRACAO da base de conhecimento (a "terceira IA") na resposta
do chat - fatia 3 (pedido do Silvan, 2026-09-29) - ver
app.ia_preparo.responder_com_ia / _aplicar_base_de_conhecimento e a coluna
"Base de conhecimento" em medico/perguntas.html.

Cobre:
1. Base desligada: nada muda (sem coluna, sem revisao extra).
2. Base ligada, IAs responderam e o arbitro diz que a base NAO diverge:
   resposta da base fica como terceira coluna, sem revisao obrigatoria.
3. Arbitro diz que DIVERGE: exige_revisao_base = True (o arbitro e chamado
   ignorando diferencas so de prazo).
4. IAs sem resposta + base achou: o rascunho vira a resposta da base, sempre
   com revisao do medico.
5. Sem tipo de exame no preparo / pergunta sem parecido / mensagem sem
   sentido: a base nao entra.
6. Uma falha na base nunca derruba o chat.
7. O prompt do arbitro so manda ignorar prazos quando pedido.
8. Tela de aprovacao do medico mostra a coluna, a fonte e o aviso.

Sem rede (IAs e arbitro simulados). Precisa do banco recriado + seed.py, como
os demais testes. Para rodar:
    DATABASE_URL=sqlite:///teste_base_integracao.db python seed.py
    DATABASE_URL=sqlite:///teste_base_integracao.db python test_base_integracao_chat.py
"""
from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, Paciente, Exame, PerguntaPendente, PreparoModelo, TipoExame,
    BaseConhecimentoItem, PlataformaConfig,
)
from app.tipos_exame_padrao import semear_tipos_exame
import app.ia_preparo as ia

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


with app.app_context():
    semear_tipos_exame(db, TipoExame)
    clinica = Grupo.query.filter_by(nome="Clínica Vitória").first()
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    paciente = Paciente.query.filter_by(cpf="123.456.789-00").first()
    clinica_id, carlos_id, paciente_id = clinica.id, carlos.id, paciente.id
    colono = TipoExame.query.filter_by(nome="Colonoscopia").first()

    modelo_com_tipo = PreparoModelo(nome="Preparo Com Tipo Teste", instrucoes="x", grupo_id=clinica_id,
                                    criado_por_id=carlos_id, tipo_exame_id=colono.id)
    modelo_sem_tipo = PreparoModelo(nome="Preparo Sem Tipo Teste", instrucoes="x", grupo_id=clinica_id,
                                    criado_por_id=carlos_id)
    db.session.add_all([modelo_com_tipo, modelo_sem_tipo])
    db.session.flush()
    exame_com_tipo = Exame(grupo_id=clinica_id, medico_id=carlos_id, nome="Exame Com Tipo Teste",
                           associado=True, medico_confirmado=True, preparo_modelo_id=modelo_com_tipo.id)
    exame_sem_tipo = Exame(grupo_id=clinica_id, medico_id=carlos_id, nome="Exame Sem Tipo Teste",
                           associado=True, medico_confirmado=True, preparo_modelo_id=modelo_sem_tipo.id)
    item = BaseConhecimentoItem(
        tipo_exame_id=colono.id, pergunta="Posso mascar chiclete durante o preparo?",
        resposta="Não. Chiclete conta como quebra da restrição do preparo.",
        fonte_nome="Fonte Teste", fonte_url="https://exemplo.test/fonte", origem="dono", revisado=True,
    )
    db.session.add_all([exame_com_tipo, exame_sem_tipo, item])
    db.session.commit()
    exame_com_tipo_id, exame_sem_tipo_id, item_id = exame_com_tipo.id, exame_sem_tipo.id, item.id

PERGUNTA = "posso mascar chiclete no preparo"
chamadas_arbitro = []
cenario = {"final": "Resposta das IAs sobre chiclete.", "sem_sentido": False, "diverge": False}


def duas_ias_falsas(pergunta, exame, paciente_id=None, historico=None):
    return {
        "final": cenario["final"],
        "por_provedor": {"Claude": cenario["final"], "ChatGPT": cenario["final"], "Gemini": None, "Base": None},
        "falhas": [], "sem_sentido": cenario["sem_sentido"], "exige_revisao_medicamento": False,
    }


def arbitro_falso(cliente, a, b, paciente_id=None, ignorar_prazos=False):
    chamadas_arbitro.append({"a": a, "b": b, "ignorar_prazos": ignorar_prazos})
    return cenario["diverge"]


originais = (ia._responder_com_duas_ias, ia._respostas_divergem, ia._cliente_anthropic)
ia._responder_com_duas_ias = duas_ias_falsas
ia._respostas_divergem = arbitro_falso
ia._cliente_anthropic = lambda: object()


def responder(exame_id, pergunta=PERGUNTA):
    with app.app_context():
        exame = Exame.query.get(exame_id)
        resultado = ia.responder_com_ia(pergunta, exame, paciente_id=paciente_id)
        db.session.commit()
        return resultado


def ligar_base(ligada):
    with app.app_context():
        PlataformaConfig.obter().base_conhecimento_ativa = ligada
        PlataformaConfig.obter().base_busca_provedor = "palavra_chave"
        db.session.commit()


try:
    # ---------- 1. Desligada ----------
    ligar_base(False)
    r = responder(exame_com_tipo_id)
    checar("Base desligada: sem resposta da base", r["por_provedor"]["Base"] is None and r["base"] is None)
    checar("Base desligada: sem revisao extra", r["exige_revisao_base"] is False)
    checar("Base desligada: arbitro nem foi chamado", chamadas_arbitro == [])

    # ---------- 2. Ligada, nao diverge ----------
    ligar_base(True)
    cenario.update(final="Resposta das IAs sobre chiclete.", diverge=False, sem_sentido=False)
    r = responder(exame_com_tipo_id)
    checar("Resposta da base entra como terceira voz", r["por_provedor"]["Base"] and "chiclete" in r["por_provedor"]["Base"].lower())
    checar("Detalhes da base: item, metodo e nao divergiu", r["base"]["item_id"] == item_id and r["base"]["metodo"] == "palavra_chave" and r["base"]["divergiu"] is False)
    checar("Nao divergiu: sem revisao obrigatoria", r["exige_revisao_base"] is False)
    checar("Rascunho final continua sendo o das IAs", r["final"] == "Resposta das IAs sobre chiclete.")
    checar("Arbitro foi chamado ignorando prazos", len(chamadas_arbitro) == 1 and chamadas_arbitro[0]["ignorar_prazos"] is True)
    with app.app_context():
        checar("Uso do item foi contado", BaseConhecimentoItem.query.get(item_id).vezes_utilizada == 1)

    # ---------- 3. Diverge ----------
    cenario["diverge"] = True
    r = responder(exame_com_tipo_id)
    checar("Divergiu: revisao do medico obrigatoria", r["exige_revisao_base"] is True and r["base"]["divergiu"] is True)
    checar("Divergiu: o rascunho continua sendo o das IAs (preparo vence)", r["final"] == "Resposta das IAs sobre chiclete.")

    # ---------- 4. IAs sem resposta ----------
    cenario.update(final=None, diverge=False)
    n_antes = len(chamadas_arbitro)
    r = responder(exame_com_tipo_id)
    checar("Sem resposta das IAs: rascunho vira o da base", r["final"] and "chiclete" in r["final"].lower())
    checar("Sem resposta das IAs: revisao obrigatoria", r["exige_revisao_base"] is True and r["base"]["preencheu_lacuna"] is True)
    checar("Sem resposta das IAs: nao precisa de arbitro", len(chamadas_arbitro) == n_antes)

    # ---------- 5. Casos em que a base nao entra ----------
    cenario.update(final="Resposta das IAs.", diverge=False)
    r = responder(exame_sem_tipo_id)
    checar("Preparo sem tipo de exame: base nao entra", r["por_provedor"]["Base"] is None and r["exige_revisao_base"] is False)
    r = responder(exame_com_tipo_id, pergunta="qual a cor do gatorade")
    checar("Pergunta sem parecido: base nao entra", r["por_provedor"]["Base"] is None)
    cenario.update(final=None, sem_sentido=True)
    r = responder(exame_com_tipo_id)
    checar("Mensagem sem sentido: base nao entra", r["final"] is None and r["por_provedor"]["Base"] is None)
    cenario.update(sem_sentido=False, final="Resposta das IAs.")

    # ---------- 6. Falha na base nao derruba o chat ----------
    import app.base_conhecimento as bc
    original_buscar = bc.buscar_na_base

    def buscar_quebrado(*a, **k):
        raise RuntimeError("falha simulada")

    bc.buscar_na_base = buscar_quebrado
    try:
        r = responder(exame_com_tipo_id)
    finally:
        bc.buscar_na_base = original_buscar
    checar("Falha na base: chat segue com as IAs", r["final"] == "Resposta das IAs." and r["exige_revisao_base"] is False)
finally:
    ia._responder_com_duas_ias, ia._respostas_divergem, ia._cliente_anthropic = originais

# ---------- 7. Prompt do arbitro ----------
class ClienteFalso:
    def __init__(self):
        self.systems = []
        outer = self

        class Mensagens:
            def create(self, **kw):
                outer.systems.append(kw["system"])
                class Bloco:
                    text = "SIM"
                class Resp:
                    content = [Bloco()]
                    usage = None
                    model = "teste"
                return Resp()
        self.messages = Mensagens()


with app.app_context():
    cliente = ClienteFalso()
    ia._respostas_divergem(cliente, "a", "b")
    ia._respostas_divergem(cliente, "a", "b", ignorar_prazos=True)
    checar("Arbitro padrao NAO manda ignorar prazos", "IGNORE" not in cliente.systems[0])
    checar("Arbitro com ignorar_prazos manda ignorar prazos", "IGNORE" in cliente.systems[1])

# ---------- 8. Tela do medico ----------
with app.app_context():
    db.session.add(PerguntaPendente(
        grupo_id=clinica_id, paciente_id=paciente_id, exame_id=exame_com_tipo_id,
        pergunta="Pergunta com base divergente", status="aguardando_aprovacao",
        resposta_sugerida_ia="Rascunho das IAs", resposta_bruta_claude="Rascunho das IAs",
        resposta_bruta_base="Texto vindo da base", base_item_id=item_id, base_divergiu=True,
    ))
    db.session.add(PerguntaPendente(
        grupo_id=clinica_id, paciente_id=paciente_id, exame_id=exame_com_tipo_id,
        pergunta="Pergunta sem base", status="aguardando_aprovacao",
        resposta_sugerida_ia="Rascunho simples", resposta_bruta_claude="Rascunho simples",
    ))
    db.session.commit()

client.post("/login", data={"email": "medico@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)
client.post("/equipe/clinica", data={"clinica_id": str(clinica_id)}, follow_redirects=True)
html = client.get("/equipe/perguntas").get_data(as_text=True)
checar("Tela mostra a coluna da base com o texto", "Base de conhecimento" in html and "Texto vindo da base" in html)
checar("Tela mostra a fonte com link", "https://exemplo.test/fonte" in html and "Fonte Teste" in html)
checar("Tela mostra o aviso de divergencia", "Base diverge" in html)
checar("A pergunta sem base continua aparecendo normalmente", "Pergunta sem base" in html and "Rascunho simples" in html)

print("\nTodos os testes passaram.")
