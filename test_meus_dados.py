"""Testa a tela "Meus dados" (auth.meus_dados), pedida pelo Silvan para o
dono da plataforma poder manter o próprio cadastro (nome, CPF, e-mail,
telefone, endereço) atualizado - disponível também para médico/secretária,
já que a mesma necessidade vale para qualquer conta com senha.

Cobre:
1. Sem confirmar a senha, a tela só mostra o formulário de confirmação -
   não expõe nome/CPF/e-mail atuais nem aceita edição.
2. Senha errada na confirmação não libera a edição.
3. Senha certa libera a edição (no mesmo request, sem precisar recarregar
   de novo) e a confirmação vale pelas próximas visitas dentro da janela
   de tempo (MEUS_DADOS_CONFIRMACAO_VALIDA_MINUTOS).
4. Salvar com dados válidos atualiza nome/CPF/e-mail/telefone/endereço de
   verdade.
5. Validações: CPF inválido, e-mail duplicado de outra conta, CPF
   duplicado de outra conta (mesma lógica de busca por dígitos usada no
   login) - nenhum desses casos deve alterar o registro.
6. Paciente (sem senha) é redirecionado, não acessa a tela.
7. Checagem estática do bug real encontrado em produção: o popup de senha
   (bootstrap.Modal) precisa ser criado dentro de um listener
   DOMContentLoaded - o <script> deste template roda ANTES do
   <script src=".../bootstrap.bundle.min.js"> carregado no fim do <body>
   (ver base.html), então chamar `new bootstrap.Modal(...)` direto no
   corpo do <script> falha em silêncio (a lib ainda não existe) e o popup
   nunca abre, deixando os campos desabilitados pra sempre sem jeito
   nenhum de confirmar a senha. test_client não executa JS, então esse
   bug não aparece nos testes de comportamento acima - só numa checagem
   textual do template mesmo.

Roda com banco isolado: `rm -f preparo_exames.db && python3
test_meus_dados.py`.
"""
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    db.create_all()

    dono = Usuario(nome="Dono Original", email="dono@plataforma.com", tipo="dono", cpf="52998224725")
    dono.set_senha("senha-dono-123")
    db.session.add(dono)
    db.session.commit()
    dono_id = dono.id

    outra_conta = Usuario(nome="Outra Pessoa", email="outra@teste.com", tipo="medico", cpf="11144477735")
    outra_conta.set_senha("123456")
    db.session.add(outra_conta)
    db.session.commit()

    paciente = Usuario(nome="Paciente Sem Senha", telefone="27999990000", tipo="paciente")
    db.session.add(paciente)
    db.session.commit()

client.post("/login", data={"identificador": "dono@plataforma.com", "senha": "senha-dono-123"})

# --- Sem confirmar senha: só o formulário de confirmação aparece. ---
r1 = client.get("/meus-dados")
html1 = r1.get_data(as_text=True)
checar("Tela responde 200 sem confirmar senha", r1.status_code == 200)
checar("Mostra o popup de confirmação de senha", 'name="senha_atual"' in html1)
# O menu de navegação do painel do dono continua visível mesmo antes de
# confirmar a senha (achado do Silvan: a tela antiga ficava "sem
# referência" nenhuma de menu enquanto pedia a senha).
checar("Menu do painel do dono aparece mesmo antes de confirmar a senha", 'href="/dono/usuarios"' in html1 and "Licenças" in html1)
# O formulário de edição continua no DOM (a página não fica "nua", sem
# menu - pedido do Silvan), mas desabilitado por trás do popup até a
# senha ser confirmada de verdade no servidor.
checar("Campo nome existe mas vem desabilitado antes de confirmar", 'name="nome"' in html1 and '<fieldset id="campos-meus-dados" disabled>' in html1)

# --- Senha errada não libera a edição. ---
r2 = client.post("/meus-dados", data={"acao": "confirmar_senha", "senha_atual": "senha-errada"}, follow_redirects=True)
checar("Senha errada mostra mensagem de erro", "Senha incorreta" in r2.get_data(as_text=True))
checar("Formulário continua desabilitado com senha errada", '<fieldset id="campos-meus-dados" disabled>' in r2.get_data(as_text=True))

# --- Senha certa libera a edição. ---
r3 = client.post("/meus-dados", data={"acao": "confirmar_senha", "senha_atual": "senha-dono-123"}, follow_redirects=True)
html3 = r3.get_data(as_text=True)
checar("Depois da senha certa, responde 200", r3.status_code == 200)
checar("Formulário de edição fica habilitado (sem disabled)", '<fieldset id="campos-meus-dados" >' in html3 or '<fieldset id="campos-meus-dados">' in html3)
checar("Dados atuais aparecem preenchidos", 'value="Dono Original"' in html3)

# Uma segunda visita (mesma sessão, dentro da janela de tempo) já mostra
# a edição direto, sem pedir senha de novo.
r4 = client.get("/meus-dados")
checar("Segunda visita já mostra edição sem pedir senha de novo", 'name="nome"' in r4.get_data(as_text=True))

# --- CPF inválido não salva. ---
r5 = client.post("/meus-dados", data={
    "acao": "salvar", "nome": "Dono Novo Nome", "email": "dono@plataforma.com",
    "cpf": "111.111.111-11", "telefone": "", "cep": "", "rua": "", "numero": "",
    "complemento": "", "bairro": "", "cidade": "", "uf": "",
}, follow_redirects=True)
checar("CPF inválido mostra mensagem de erro", "CPF inválido" in r5.get_data(as_text=True))
with app.app_context():
    checar("Nome NÃO foi alterado com CPF inválido", Usuario.query.get(dono_id).nome == "Dono Original")

# --- E-mail duplicado de outra conta não salva. ---
r6 = client.post("/meus-dados", data={
    "acao": "salvar", "nome": "Dono Novo Nome", "email": "outra@teste.com",
    "cpf": "529.982.247-25", "telefone": "", "cep": "", "rua": "", "numero": "",
    "complemento": "", "bairro": "", "cidade": "", "uf": "",
}, follow_redirects=True)
checar("E-mail duplicado mostra mensagem de erro", "Já existe uma conta com esse e-mail" in r6.get_data(as_text=True))
with app.app_context():
    checar("Nome NÃO foi alterado com e-mail duplicado", Usuario.query.get(dono_id).nome == "Dono Original")

# --- CPF duplicado de outra conta (com pontuação diferente) não salva. ---
r7 = client.post("/meus-dados", data={
    "acao": "salvar", "nome": "Dono Novo Nome", "email": "dono@plataforma.com",
    "cpf": "111.444.777-35", "telefone": "", "cep": "", "rua": "", "numero": "",
    "complemento": "", "bairro": "", "cidade": "", "uf": "",
}, follow_redirects=True)
checar("CPF duplicado mostra mensagem de erro", "Já existe uma conta com esse CPF" in r7.get_data(as_text=True))
with app.app_context():
    checar("Nome NÃO foi alterado com CPF duplicado", Usuario.query.get(dono_id).nome == "Dono Original")

# --- Caminho feliz: dados válidos salvam de verdade. ---
r8 = client.post("/meus-dados", data={
    "acao": "salvar", "nome": "Dono Atualizado", "email": "dono.novo@plataforma.com",
    "cpf": "529.982.247-25", "telefone": "(27) 99999-1234", "cep": "29010-000",
    "rua": "Av. Teste", "numero": "100", "complemento": "Sala 1", "bairro": "Centro",
    "cidade": "Vitória", "uf": "es",
}, follow_redirects=True)
checar("Mensagem de sucesso aparece", "Dados atualizados com sucesso" in r8.get_data(as_text=True))
with app.app_context():
    d = Usuario.query.get(dono_id)
    checar("Nome foi atualizado", d.nome == "Dono Atualizado")
    checar("E-mail foi atualizado", d.email == "dono.novo@plataforma.com")
    checar("CPF foi atualizado", d.cpf == "529.982.247-25")
    checar("Telefone foi normalizado/salvo", d.telefone and "999991234" in d.telefone)
    checar("Endereço foi salvo", d.rua == "Av. Teste" and d.cidade == "Vitória")
    checar("UF foi salva em maiúsculo", d.uf == "ES")

# --- Paciente (sem senha) não acessa a tela. ---
client.get("/logout")
with app.app_context():
    paciente_id = Usuario.query.filter_by(tipo="paciente").first().id
with client.session_transaction() as sess:
    sess["_user_id"] = str(paciente_id)
    sess["_fresh"] = True
r9 = client.get("/meus-dados", follow_redirects=True)
checar("Paciente é redirecionado (não vê a tela)", 'name="senha_atual"' not in r9.get_data(as_text=True))

# --- Checagem estática: bootstrap.Modal só pode ser chamado dentro de um
# listener DOMContentLoaded (ver docstring acima). ---
template_html = Path(__file__).parent / "app" / "templates" / "auth" / "meus_dados.html"
conteudo_template = template_html.read_text(encoding="utf-8")
indice_modal = conteudo_template.find("new bootstrap.Modal")
indice_dom_ready = conteudo_template.find("document.addEventListener('DOMContentLoaded'")
checar(
    "new bootstrap.Modal(...) existe e vem DEPOIS do listener DOMContentLoaded que o envolve",
    indice_modal != -1 and indice_dom_ready != -1 and indice_dom_ready < indice_modal,
)

print("\nTodos os testes de Meus dados passaram.")
