"""Testa a correção do bug relatado: "Cadastrei como médico mas não
entrou na lista de pessoas da equipe". O fundador da empresa (cadastro
público) não tem vínculo de filial nenhum (cadastrar local não vincula
ninguém automaticamente) - e a tela "Equipe" só listava quem tinha
ClinicaMembro, então o fundador não aparecia em lugar nenhum ("Nenhum
membro cadastrado", mesmo com o Bruno logado ali no topo).

Regra corrigida: quem fundou a empresa (Usuario.empresa_fundadora_id) faz
parte da equipe desde o cadastro, com ou sem vínculo de local - aparece
na lista (com "Não atua em nenhum local ainda"), pode ser associado a
filiais pelo atalho da própria tela, e pode ser editado/ter permissões
ajustadas normalmente."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Cenário exato do usuário: fundador médico, empresa com 2 locais, zero
# vínculos. O cadastro público não cria nenhum local nem vínculo - é
# exatamente o cenário do bug, sem precisar recriá-lo à mão.
r = client.post("/cadastro", data={
    "nome": "Bruno Pavan",
    "cpf": "852.963.741-00", "crm_numero": "22222", "crm_uf": "ES",
    "email": "bruno.equipe@example.com",
    "senha": "123456",
    "papel": "medico",
}, follow_redirects=True)
checar("Cadastro do fundador responde 200", r.status_code == 200)
client.post("/equipe/filiais/nova", data={"nome": "MG Centro"}, follow_redirects=True)
client.post("/equipe/filiais/nova", data={"nome": "MG Santa Lucia"}, follow_redirects=True)

with app.app_context():
    # O cadastro não cria mais nenhum local - a empresa nasce com um nome
    # provisório a partir do nome da própria pessoa.
    empresa = Empresa.query.filter_by(nome="Consultório de Bruno Pavan").first()
    bruno = Usuario.query.filter_by(email="bruno.equipe@example.com").first()
    bruno_id = bruno.id
    centro_id = Clinica.query.filter_by(empresa_id=empresa.id, nome="MG Centro").first().id
    checar("Fundador não tem vínculo nenhum (cenário do bug)",
           ClinicaMembro.query.filter_by(usuario_id=bruno_id, ativo=True).count() == 0)

# ---------- O fundador aparece na lista da Equipe ----------

r = client.get("/equipe/equipe-membros")
html = r.get_data(as_text=True)
checar("Fundador aparece na lista da Equipe mesmo sem vínculo", "Bruno Pavan" in html)
checar("NÃO mostra mais 'Nenhum membro cadastrado'", "Nenhum membro cadastrado" not in html)
checar("Linha do fundador indica que ele não atua em nenhum local ainda",
       "Não atua em nenhum local ainda" in html)
checar("Linha do fundador oferece o atalho de associar a uma filial",
       "Associar a outra filial" in html)

# ---------- Editar e permissões funcionam para o fundador sem vínculo ----------

r = client.get(f"/equipe/equipe-membros/{bruno_id}/editar")
checar("Tela de editar o fundador abre normalmente (não dá 'não encontrada')", r.status_code == 200)

r = client.post(f"/equipe/equipe-membros/{bruno_id}/editar", data={
    "nome": "Bruno Pavan Filho",
    # nenhuma filial marcada - fundador pode ficar sem vínculo
}, follow_redirects=True)
checar("Salvar edição do fundador sem marcar filial funciona", "atualizados" in r.get_data(as_text=True))
with app.app_context():
    checar("Nome foi salvo", Usuario.query.get(bruno_id).nome == "Bruno Pavan Filho")

r = client.get(f"/equipe/equipe-membros/{bruno_id}/permissoes")
checar("Tela de permissões do fundador abre normalmente", r.status_code == 200)

# ---------- Associar o fundador a uma filial pelo atalho da tela ----------

r = client.post(f"/equipe/equipe-membros/{bruno_id}/associar-filial",
                data={"filial_id": str(centro_id)}, follow_redirects=True)
checar("Associar o fundador a uma filial pelo atalho funciona", "vinculado" in r.get_data(as_text=True).lower())
with app.app_context():
    checar("O vínculo foi criado",
           ClinicaMembro.query.filter_by(usuario_id=bruno_id, clinica_id=centro_id).count() == 1)

r = client.get("/equipe/equipe-membros")
html2 = r.get_data(as_text=True)
checar("Agora a filial aparece na linha do fundador", "MG Centro" in html2)
checar("O aviso de 'não atua em nenhum local' sumiu", "Não atua em nenhum local ainda" not in html2)

# ---------- Quem NÃO é da empresa continua de fora ----------

client.get("/logout")
client.post("/login", data={"email": "secretaria@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)
r = client.get("/equipe/equipe-membros")
checar("Fundador de outra empresa NÃO aparece na equipe da Clínica Vitória",
       "Bruno Pavan" not in r.get_data(as_text=True))
r = client.get(f"/equipe/equipe-membros/{bruno_id}/permissoes", follow_redirects=True)
checar("Outra empresa não consegue abrir as permissões do fundador alheio",
       "não encontrada" in r.get_data(as_text=True).lower() or "Bruno" not in r.get_data(as_text=True))
client.get("/logout")

print("\nTodos os testes do fundador na equipe passaram.")
