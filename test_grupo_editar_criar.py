"""Grupo de trabalho: editar os dados e criar outro (relatado pelo Silvan,
2026-10-20). Quem tem um grupo só cai direto em "Convidar membros" (ver
grupo.meus_grupos), e dali não havia como editar o grupo nem criar outro:
o link "Meus grupos" voltava para a mesma tela.

- "Convidar membros" ganha "Dados do grupo" (Dados Cadastrais do grupo
  ativo) e "Criar outro grupo".
- O link "Meus grupos" (?lista=1) mostra a lista, com "Criar novo grupo"."""
from app import create_app
from app.models import Grupo, GrupoMembro, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/cadastro", data={
    "nome": "Medico Grupo Unico", "email": "grupo.unico@teste.com", "senha": "123456", "senha_confirmacao": "123456",
    "cpf": "529.982.247-25", "telefone": "(27) 98888-2222", "papel": "medico",
    "crm_numero": "22222", "crm_uf": "ES", "data_nascimento": "01/01/1980",
}, follow_redirects=True)
r = client.post("/grupos/novo", data={"nome": "Clinica Primeira"}, follow_redirects=True)
with app.app_context():
    usuario = Usuario.query.filter_by(email="grupo.unico@teste.com").first()
    grupo = Grupo.query.filter_by(nome="Clinica Primeira").first()
    checar("Grupo criado e a pessoa é dona dele",
           grupo is not None and GrupoMembro.query.filter_by(grupo_id=grupo.id, usuario_id=usuario.id, papel="dono").first())
    grupo_id = grupo.id

r = client.get("/grupos/")
html = r.get_data(as_text=True)
checar("Com um grupo só, 'Grupos de trabalho' abre a lista (não pula mais para Convidar membros)",
       r.status_code == 200 and "Meus grupos" in html and "Clinica Primeira" in html)
checar("A lista tem 'Criar novo grupo', 'Dados do grupo' e 'Ver membros / Convidar'",
       "Criar novo grupo" in html and "Dados do grupo" in html and f"/grupos/{grupo_id}/convidar" in html)

r = client.get(f"/equipe/clinica/configuracoes/{grupo_id}")
html = r.get_data(as_text=True)
checar("'Dados do grupo' abre só com o nome do grupo", r.status_code == 200 and 'value="Clinica Primeira"' in html)
checar("... sem razão social, CNPJ, telefone, e-mail, endereço nem Dados Fiscais",
       not any(f'name="{c}"' in html for c in ["razao_social", "cnpj", "telefone", "email_contato", "cep", "rua"])
       and "Dados Fiscais" not in html)
r = client.post(f"/equipe/clinica/configuracoes/{grupo_id}", data={"nome": "Clinica Renomeada"})
checar("Salvar volta para Meus grupos", r.status_code == 302 and r.headers["Location"].endswith("/grupos/"))
with app.app_context():
    checar("Editar os dados do grupo salva (nome novo)", Grupo.query.get(grupo_id).nome == "Clinica Renomeada")

r = client.post("/grupos/novo", data={"nome": "Clinica Segunda"}, follow_redirects=True)
with app.app_context():
    checar("Criar outro grupo funciona: agora são dois",
           GrupoMembro.query.filter_by(usuario_id=usuario.id, ativo=True).count() == 2)
r = client.get("/grupos/")
checar("Com dois grupos, 'Grupos de trabalho' mostra a lista", r.status_code == 200 and "Clinica Segunda" in r.get_data(as_text=True))
checar("... com 'Dados do grupo' no grupo ativo", "Dados do grupo" in r.get_data(as_text=True))

# ---------- Permissões: "Gerenciar filiais" saiu, "dados do grupo" renomeada ----------
with app.app_context():
    usuario_id = Usuario.query.filter_by(email="grupo.unico@teste.com").first().id
html = client.get(f"/equipe/equipe-membros/{usuario_id}/permissoes", follow_redirects=True).get_data(as_text=True)
checar("Permissões não têm mais 'Gerenciar filiais'", "Gerenciar filiais" not in html and 'name="perm_filiais"' not in html)
checar("'Editar dados da clínica' virou 'Editar dados do grupo (cadastrais e fiscais)'",
       "Editar dados do grupo" in html and "Editar dados da clínica" not in html)

print("\nTodos os testes de editar/criar grupo passaram.")
