"""Testa a correção do bug relatado: o cadastro público (modo "empresa")
criava automaticamente uma filial técnica ("Matriz") já vinculada à
pessoa que se cadastrou, mostrando o badge "você atende aqui" em "Meus
Locais de Atendimento" mesmo sem ela ter cadastrado/confirmado nada -
"Ao fazer o cadastro, somente cadastra os dados da empresa. A filial será
cadastrada ao entrar no app" e "não quer dizer que cadastrei a filial que
atendo aqui".

Agora o cadastro público (modo "empresa") cria SÓ a Empresa e o Usuario -
nenhuma Clinica, nenhum ClinicaMembro. A pessoa só passa a "atuar" numa
filial de verdade quando ela mesma cadastra uma em "Meus Locais de
Atendimento" (medico.filiais_nova). Até lá, o vínculo com a empresa é
feito por Usuario.empresa_fundadora_id (não por ClinicaMembro), e todo o
app precisa continuar funcionando normalmente com zero filiais.

Segunda e terceira rodadas do mesmo bug, reportadas de novo depois da
primeira correção: mesmo cadastrando o local DELIBERADAMENTE (sem nenhuma
filial fantasma automática), o formulário ainda vinculava a pessoa a ele -
primeiro de forma invisível/implícita, depois via um checkbox marcado por
padrão, e o usuário rejeitou os dois ("Ainda tá aqui!!!!", "Ainda continua
cadastrando errado"). Regra final: cadastrar um local NUNCA vincula
ninguém a ele, em nenhuma hipótese - "quem atua em qual local" é sempre
uma associação separada e explícita, pelo botão "+ Marcar que você também
atua aqui" em "Meus Locais de Atendimento" (medico.filiais_vincular_me)
para a própria pessoa, ou pela tela "Equipe" para qualquer pessoa. Também
existe a ação inversa ("desmarcar", medico.filiais_desvincular_me) para
desfazer vínculos errados criados pelas versões antigas do fluxo. E a
palavra "atende" (implica atendimento clínico) virou "atua" (o termo
genérico já usado em Equipe para vínculo administrativo/de acesso)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


CPF_BRUNO = "852.963.741-00"  # CPF VÁLIDO (dígitos verificadores conferem)

r = client.post("/cadastro", data={
    "nome": "Bruno Pavan",
    "cpf": CPF_BRUNO,
    "crm_numero": "11111", "crm_uf": "ES",
    "email": "bruno.pavan@medicalgastro.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Medical Gastro - Matriz",
    "telefone_filial": "(27) 90000-0001",
    "cnpj_filial": "12.345.603/0001-04",
}, follow_redirects=True)
checar("Cadastro responde 200 e cai no assistente de configuração inicial", r.status_code == 200 and "Configuração inicial" in r.get_data(as_text=True))

with app.app_context():
    # Não existe mais um "nome da empresa" separado do nome do local - a
    # empresa nasce com o mesmo nome informado pro local de atendimento.
    empresa = Empresa.query.filter_by(nome="Medical Gastro - Matriz").first()
    checar("Empresa foi criada", empresa is not None)
    usuario = Usuario.query.filter_by(email="bruno.pavan@medicalgastro.com").first()
    checar("Usuário foi criado", usuario is not None)
    checar("Usuário recebeu todas as permissões administrativas", usuario.perm_filiais and usuario.perm_dados_clinica)
    # Mudança de comportamento (pedido explícito do usuário): o cadastro
    # agora já cria o primeiro local de atendimento completo (nome,
    # telefone, endereço) e vincula quem se cadastrou a ele - não fica
    # mais pra depois, em "Meus locais de atendimento".
    checar("O primeiro local de atendimento JÁ foi criado, com os dados completos",
           Clinica.query.filter_by(empresa_id=empresa.id, nome="Medical Gastro - Matriz").count() == 1)
    checar("Quem se cadastrou JÁ fica vinculado a esse primeiro local",
           ClinicaMembro.query.filter_by(usuario_id=usuario.id).count() == 1)
    checar("Usuário fica vinculado à empresa via empresa_fundadora_id", usuario.empresa_fundadora_id == empresa.id)
    empresa_id = empresa.id

# O app inteiro precisa continuar funcionando normalmente a partir daqui -
# painel, assistente de configuração inicial e "Meus locais de atendimento".
r_painel = client.get("/equipe/")
checar("Painel responde 200", r_painel.status_code == 200)

r_onboarding = client.get("/equipe/configuracao-inicial")
checar("Assistente de configuração inicial responde 200", r_onboarding.status_code == 200)

r_locais = client.get("/equipe/filiais")
html_locais = r_locais.get_data(as_text=True)
checar("'Meus locais de atendimento' responde 200", r_locais.status_code == 200)
checar("O primeiro local (criado no cadastro) já aparece na lista", "Medical Gastro - Matriz" in html_locais)

# Acessar "Dados Cadastrais"/"Dados Fiscais" já funciona normalmente, pois
# o primeiro local já existe.
r_dados = client.get("/equipe/clinica/configuracoes", follow_redirects=True)
checar("Dados Cadastrais funciona normalmente (o local já existe)", r_dados.status_code == 200)

r_fiscais = client.get("/equipe/clinica/dados-fiscais", follow_redirects=True)
checar("Dados Fiscais funciona normalmente (o local já existe)", r_fiscais.status_code == 200)

# Cadastrar um SEGUNDO local continua sem vincular ninguém a ele
# automaticamente - essa regra (motivada por reclamações antigas do
# usuário sobre um vínculo implícito indesejado) continua valendo para os
# próximos locais, mesmo que o primeiro já venha vinculado pelo cadastro.
r_novo_local = client.post(
    "/equipe/filiais/nova",
    data={"nome": "Unidade Praia do Canto", "vincular_a_mim": "on"},
    follow_redirects=True,
)
checar("Cadastro do segundo local responde 200", r_novo_local.status_code == 200)
checar("Mensagem de sucesso aparece", "cadastrado com sucesso" in r_novo_local.get_data(as_text=True).lower())
checar(
    "A mensagem já avisa que cadastrar o local não vincula ninguém",
    "não vincula ninguém" in r_novo_local.get_data(as_text=True),
)

with app.app_context():
    filial = Clinica.query.filter_by(empresa_id=empresa_id, nome="Unidade Praia do Canto").first()
    checar("A filial foi criada de verdade", filial is not None)
    checar(
        "NENHUM ClinicaMembro foi criado - cadastrar local NUNCA vincula ninguém (nem com campo extra no POST)",
        ClinicaMembro.query.filter_by(clinica_id=filial.id).count() == 0,
    )
    filial_id = filial.id

r_locais_intermed = client.get("/equipe/filiais")
html_locais_intermed = r_locais_intermed.get_data(as_text=True)
checar("Filial cadastrada aparece na lista", "Unidade Praia do Canto" in html_locais_intermed)
# O fundador já está vinculado ao primeiro local (Matriz, criado pelo
# cadastro) - por isso já existe UM badge na lista. O local recém-criado
# (segundo) não ganha um badge extra automaticamente, só o botão explícito.
checar(
    "Só existe o badge do primeiro local (Matriz) - o recém-criado não ganhou badge automático",
    html_locais_intermed.count('badge bg-primary ms-1">você atua aqui') == 1
    and "Marcar que você também atua aqui" in html_locais_intermed,
)

# A pessoa então se vincula deliberadamente pelo botão - só isso cria o vínculo.
r_vincular_me = client.post(f"/equipe/filiais/{filial_id}/vincular-me", follow_redirects=True)
checar("Vincular-se pelo botão responde 200", r_vincular_me.status_code == 200)
with app.app_context():
    checar(
        "Agora o ClinicaMembro existe, criado por uma ação deliberada e explícita",
        ClinicaMembro.query.filter_by(clinica_id=filial_id, usuario_id=usuario.id).count() == 1,
    )

# Agora sim, "Meus locais de atendimento" mostra o badge nos DOIS locais -
# porque a pessoa de fato marcou, deliberadamente, que atua no segundo também.
r_locais2 = client.get("/equipe/filiais")
html_locais2 = r_locais2.get_data(as_text=True)
checar("Agora mostra 'você atua aqui' nos dois locais (foi uma escolha deliberada, pelo botão)",
       html_locais2.count('badge bg-primary ms-1">você atua aqui') == 2)
checar("Ao lado do badge existe a ação inversa ('desmarcar')", "desvincular-me" in html_locais2)

# Dados Cadastrais agora funciona normalmente para essa filial.
r_dados2 = client.get("/equipe/clinica/configuracoes")
checar("Dados Cadastrais funciona normalmente depois de cadastrar o local", r_dados2.status_code == 200)
checar("NÃO mostra mais o aviso de 'cadastre seu primeiro local'", "Cadastre seu primeiro local de atendimento" not in r_dados2.get_data(as_text=True))

# A ação inversa desfaz o vínculo (é assim que se corrige um badge errado
# criado pelas versões antigas do fluxo). O fundador pode remover até o
# último vínculo sem se trancar fora (a âncora empresa_fundadora_id
# continua resolvendo o acesso dele).
r_desvincular = client.post(f"/equipe/filiais/{filial_id}/desvincular-me", follow_redirects=True)
checar("Desmarcar o vínculo responde 200", r_desvincular.status_code == 200)
with app.app_context():
    # A desvinculação agora ENCERRA o vínculo em vez de apagar (histórico
    # preservado - ver ClinicaMembro.encerrado_em).
    vinculo_encerrado = ClinicaMembro.query.filter_by(clinica_id=filial_id, usuario_id=usuario.id).first()
    checar(
        "O ClinicaMembro foi encerrado (ativo=False, registro preservado)",
        vinculo_encerrado is not None and vinculo_encerrado.ativo is False
        and vinculo_encerrado.encerrado_em is not None,
    )
r_locais3 = client.get("/equipe/filiais")
checar(
    "O badge do segundo local sumiu depois de desmarcar (só sobra o da Matriz)",
    r_locais3.get_data(as_text=True).count('badge bg-primary ms-1">você atua aqui') == 1,
)
checar("Fundador continua com acesso normal ao painel", client.get("/equipe/").status_code == 200)

client.get("/logout")

# Quem NÃO é fundador não pode remover o próprio ÚLTIMO vínculo (ficaria
# trancado fora da conta) - usa a Clínica Vitória do seed pra testar.
client.post("/login", data={"email": "secretaria@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)
with app.app_context():
    vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id
r_bloqueado = client.post(f"/equipe/filiais/{vitoria_id}/desvincular-me", follow_redirects=True)
checar(
    "Não-fundador com um único vínculo é impedido de se desvincular (não se tranca fora)",
    "único vínculo" in r_bloqueado.get_data(as_text=True),
)
with app.app_context():
    secretaria = Usuario.query.filter_by(email="secretaria@clinicavitoria.com").first()
    checar(
        "O vínculo da secretária continua intacto",
        ClinicaMembro.query.filter_by(usuario_id=secretaria.id, clinica_id=vitoria_id).count() == 1,
    )
client.get("/logout")

print("\nTodos os testes de cadastro de empresa/local sem vínculo automático passaram.")
