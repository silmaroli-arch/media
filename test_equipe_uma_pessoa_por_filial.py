"""Testa que a tela "Equipe" mostra uma única linha por PESSOA (não uma
linha repetida por filial) quando a mesma pessoa atua em mais de uma filial
da empresa - antes cada vínculo aparecia como uma linha inteira separada,
o que parecia (incorretamente) um cadastro duplicado, mesmo sendo sempre
uma única conta (Usuario) com vários vínculos (ClinicaMembro). Também
testa o atalho "+ Associar a outra filial" direto na tela, e que remover
um vínculo específico não afeta os outros vínculos da mesma pessoa."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, ClinicaMembro, Empresa

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    secretaria_vitoria = Usuario.query.filter_by(email="secretaria@clinicavitoria.com").first()
    clinica_vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    empresa_id = clinica_vitoria.empresa_id

    # Cria uma segunda filial na mesma empresa da Clínica Vitória, só para
    # este teste (a empresa "Clínica Vitória" no seed tem só uma filial).
    filial_extra = Clinica(
        empresa_id=empresa_id, nome="Clínica Vitória - Filial Sul",
        cnpj="00.000.000/0002-00", email_contato="filialsul@clinicavitoria.com",
    )
    db.session.add(filial_extra)
    db.session.commit()

    medico_vitoria = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    medico_id = medico_vitoria.id
    filial_extra_id = filial_extra.id
    clinica_vitoria_id = clinica_vitoria.id
    secretaria_vitoria.perm_equipe = True
    db.session.commit()

login("secretaria@clinicavitoria.com", "123456")

# Antes de associar, o médico só aparece com uma filial.
r0 = client.get("/equipe/equipe-membros")
html0 = r0.get_data(as_text=True)
checar("Tela responde 200", r0.status_code == 200)
checar("Dra. Fernanda aparece uma única vez na lista (não duplicado)", html0.count("<td>Dra. Fernanda Lima</td>") == 1)

# Associa a Dra. Fernanda à nova filial pelo atalho direto na tela.
r1 = client.post(f"/equipe/equipe-membros/{medico_id}/associar-filial", data={
    "filial_id": str(filial_extra_id),
}, follow_redirects=True)
checar("Associar a outra filial responde 200", r1.status_code == 200)
checar("Mensagem de vínculo aparece", "foi vinculado" in r1.get_data(as_text=True))

with app.app_context():
    checar(
        "Continua sendo UM ÚNICO Usuario (mesmo id) - não duplicou a conta",
        Usuario.query.filter_by(email="medica2@clinicavitoria.com").count() == 1,
    )
    checar(
        "Agora tem dois vínculos (ClinicaMembro) para o mesmo usuário",
        ClinicaMembro.query.filter_by(usuario_id=medico_id).count() == 2,
    )

# A tela continua mostrando a Dra. Fernanda numa ÚNICA linha, agora com as duas filiais.
r2 = client.get("/equipe/equipe-membros")
html2 = r2.get_data(as_text=True)
checar("Dra. Fernanda ainda aparece uma única vez (mesmo em duas filiais)", html2.count("<td>Dra. Fernanda Lima</td>") == 1)
checar("Mostra a filial original", "Clínica Vitória" in html2)
checar("Mostra a nova filial associada", "Clínica Vitória - Filial Sul" in html2)

# Tentar associar de novo à mesma filial é bloqueado (sem duplicar o vínculo).
r3 = client.post(f"/equipe/equipe-membros/{medico_id}/associar-filial", data={
    "filial_id": str(filial_extra_id),
}, follow_redirects=True)
checar("Segunda tentativa mostra aviso de duplicidade", "já faz parte dessa filial" in r3.get_data(as_text=True))
with app.app_context():
    checar("Não duplicou o vínculo", ClinicaMembro.query.filter_by(usuario_id=medico_id).count() == 2)

# Remover o vínculo com a filial nova não afeta o vínculo original.
with app.app_context():
    vinculo_novo = ClinicaMembro.query.filter_by(usuario_id=medico_id, clinica_id=filial_extra_id).first()
    vinculo_novo_id = vinculo_novo.id

r4 = client.post(f"/equipe/equipe-membros/{vinculo_novo_id}/remover", follow_redirects=True)
checar("Remover o vínculo extra responde 200", r4.status_code == 200)
with app.app_context():
    # Remover agora ENCERRA o vínculo (não apaga) - ativo sobra só o
    # original, e a conta continua intacta.
    checar(
        "Sobrou só o vínculo original ativo (não removeu a conta nem o outro vínculo)",
        ClinicaMembro.query.filter_by(usuario_id=medico_id, ativo=True).count() == 1
        and ClinicaMembro.query.filter_by(usuario_id=medico_id, clinica_id=clinica_vitoria_id, ativo=True).first() is not None,
    )
    checar("A conta do médico continua existindo normalmente", Usuario.query.get(medico_id) is not None)

client.get("/logout")
print("\nTodos os testes de uma linha por pessoa na Equipe passaram.")
