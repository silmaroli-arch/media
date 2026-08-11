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

Segunda rodada do mesmo bug, reportada de novo depois da primeira correção:
mesmo cadastrando o local DELIBERADAMENTE (sem nenhuma filial fantasma
automática), o formulário ainda vinculava a pessoa a ele de forma
invisível/implícita - "Ainda tá aqui!!!!". Corrigido tornando isso um
checkbox explícito e visível no formulário ("vincular_a_mim", marcado por
padrão só porque é o caso mais comum, não porque seja automático), e
trocando a palavra "atende" (implica atendimento clínico) por "atua"
(o termo genérico já usado em Equipe para vínculo administrativo/de
acesso). Quem desmarcar o checkbox pode se vincular depois, deliberadamente,
pelo botão "+ Marcar que você também atua aqui" em "Meus Locais de
Atendimento" (medico.filiais_vincular_me)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Medical Gastro",
    "nome": "Bruno Pavan",
    "email": "bruno.pavan@medicalgastro.com",
    "senha": "123456",
    "papel": "medico",
}, follow_redirects=True)
checar("Cadastro responde 200 e cai no assistente de configuração inicial", r.status_code == 200 and "Configuração inicial" in r.get_data(as_text=True))

with app.app_context():
    empresa = Empresa.query.filter_by(nome="Medical Gastro").first()
    checar("Empresa foi criada", empresa is not None)
    usuario = Usuario.query.filter_by(email="bruno.pavan@medicalgastro.com").first()
    checar("Usuário foi criado", usuario is not None)
    checar("Usuário recebeu todas as permissões administrativas", usuario.perm_filiais and usuario.perm_dados_clinica)
    checar("NENHUMA filial foi criada automaticamente", Clinica.query.filter_by(empresa_id=empresa.id).count() == 0)
    checar("NENHUM ClinicaMembro foi criado (usuário não 'atende' em lugar nenhum ainda)", ClinicaMembro.query.filter_by(usuario_id=usuario.id).count() == 0)
    checar("Usuário fica vinculado à empresa via empresa_fundadora_id", usuario.empresa_fundadora_id == empresa.id)
    empresa_id = empresa.id

# O app inteiro precisa continuar funcionando com zero filiais - painel,
# assistente de configuração inicial e "Meus locais de atendimento".
r_painel = client.get("/equipe/")
checar("Painel responde 200 mesmo sem nenhuma filial", r_painel.status_code == 200)

r_onboarding = client.get("/equipe/configuracao-inicial")
checar("Assistente de configuração inicial responde 200", r_onboarding.status_code == 200)

r_locais = client.get("/equipe/filiais")
html_locais = r_locais.get_data(as_text=True)
checar("'Meus locais de atendimento' responde 200", r_locais.status_code == 200)
checar("Mostra a mensagem de 'nenhum local cadastrado ainda' (não uma filial fantasma)", "Nenhum local de atendimento cadastrado" in html_locais)
checar(
    "NÃO mostra nenhum badge 'você atua aqui' (nada foi cadastrado de verdade ainda)",
    'badge bg-primary ms-1">você atua aqui' not in html_locais,
)
checar("NÃO mostra 'Matriz' nem qualquer filial técnica", "Matriz" not in html_locais)

# Acessar "Dados Cadastrais"/"Dados Fiscais" sem filial nenhuma não quebra -
# só avisa que é preciso cadastrar o primeiro local antes.
r_dados = client.get("/equipe/clinica/configuracoes", follow_redirects=True)
checar("Dados Cadastrais sem filial nenhuma não quebra (200, com aviso)", r_dados.status_code == 200)
checar("Aviso pede pra cadastrar o primeiro local antes", "Cadastre seu primeiro local de atendimento" in r_dados.get_data(as_text=True))

r_fiscais = client.get("/equipe/clinica/dados-fiscais", follow_redirects=True)
checar("Dados Fiscais sem filial nenhuma não quebra (200, com aviso)", r_fiscais.status_code == 200)

# Primeiro, cadastra um local SEM marcar "vincular_a_mim" (checkbox
# desmarcado, como quem está só organizando a estrutura pra outra pessoa
# atuar nele) - não pode vincular ninguém sem escolha explícita.
r_local_sem_vinculo = client.post(
    "/equipe/filiais/nova", data={"nome": "Unidade Jardim da Penha"}, follow_redirects=True
)
checar("Cadastro do local (sem marcar o checkbox) responde 200", r_local_sem_vinculo.status_code == 200)
with app.app_context():
    filial_sem_vinculo = Clinica.query.filter_by(empresa_id=empresa_id, nome="Unidade Jardim da Penha").first()
    checar("A filial foi criada mesmo sem vincular ninguém", filial_sem_vinculo is not None)
    checar(
        "NENHUM ClinicaMembro foi criado (checkbox não foi marcado no POST)",
        ClinicaMembro.query.filter_by(clinica_id=filial_sem_vinculo.id).count() == 0,
    )
    filial_sem_vinculo_id = filial_sem_vinculo.id

r_locais_intermed = client.get("/equipe/filiais")
html_locais_intermed = r_locais_intermed.get_data(as_text=True)
checar(
    "Local sem ninguém vinculado aparece com o botão de se vincular, não o badge",
    'badge bg-primary ms-1">você atua aqui' not in html_locais_intermed
    and "Marcar que você também atua aqui" in html_locais_intermed,
)

# A pessoa muda de ideia depois e se vincula deliberadamente pelo botão.
r_vincular_me = client.post(
    f"/equipe/filiais/{filial_sem_vinculo_id}/vincular-me", follow_redirects=True
)
checar("Vincular-se depois, pelo botão, responde 200", r_vincular_me.status_code == 200)
with app.app_context():
    checar(
        "Agora o ClinicaMembro existe, criado por uma ação deliberada e explícita",
        ClinicaMembro.query.filter_by(clinica_id=filial_sem_vinculo_id, usuario_id=usuario.id).count() == 1,
    )

# Agora a pessoa cadastra outro local DE VERDADE, deliberadamente, desta
# vez MARCANDO o checkbox "vincular_a_mim" (o padrão marcado no formulário).
r_novo_local = client.post(
    "/equipe/filiais/nova",
    data={"nome": "Unidade Praia do Canto", "vincular_a_mim": "on"},
    follow_redirects=True,
)
checar("Cadastro do local (com o checkbox marcado) responde 200", r_novo_local.status_code == 200)
checar("Mensagem de sucesso aparece", "cadastrado com sucesso" in r_novo_local.get_data(as_text=True).lower())

with app.app_context():
    filial = Clinica.query.filter_by(empresa_id=empresa_id, nome="Unidade Praia do Canto").first()
    checar("A filial foi criada de verdade", filial is not None)
    checar(
        "O ClinicaMembro foi criado porque o checkbox estava marcado (escolha explícita, não automática)",
        ClinicaMembro.query.filter_by(clinica_id=filial.id).count() == 1,
    )

# Agora sim, "Meus locais de atendimento" mostra o badge - porque a pessoa
# de fato marcou, deliberadamente, que atua nesse local.
r_locais2 = client.get("/equipe/filiais")
html_locais2 = r_locais2.get_data(as_text=True)
checar("Filial cadastrada de verdade aparece na lista", "Unidade Praia do Canto" in html_locais2)
checar("Agora SIM mostra 'você atua aqui' (foi uma escolha deliberada, com o checkbox marcado)", "você atua aqui" in html_locais2)

# Dados Cadastrais agora funciona normalmente para essa filial.
r_dados2 = client.get("/equipe/clinica/configuracoes")
checar("Dados Cadastrais funciona normalmente depois de cadastrar o local", r_dados2.status_code == 200)
checar("NÃO mostra mais o aviso de 'cadastre seu primeiro local'", "Cadastre seu primeiro local de atendimento" not in r_dados2.get_data(as_text=True))

client.get("/logout")
print("\nTodos os testes de cadastro de empresa sem filial automática/vínculo implícito passaram.")
