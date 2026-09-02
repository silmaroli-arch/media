"""
Popula o banco de dados com dados de exemplo para demonstração da
plataforma, 100% em cima de Grupo/GrupoMembro/GrupoPaciente (Fatia 5 -
Empresa/Clinica/ClinicaMembro não existem mais):
- Um usuário "dono da plataforma"
- Dois grupos de uma "unidade" só (Vitória e São Paulo), para demonstrar
  isolamento de dados entre grupos diferentes
- Um terceiro cenário com DOIS grupos que compartilham a mesma equipe
  (Grupo Saúde Total - Centro / Praia), para demonstrar alguém com
  vínculo ativo em mais de um grupo ao mesmo tempo (precisa escolher qual
  está usando no momento) e a cobrança por médico
- Um médico vinculado a dois grupos (conta uma vez em cada cobrança, já
  que cobrança agora é por grupo) e outro só num grupo
- Secretárias, pacientes, exames, agendamentos e FAQ por grupo

Rodar com: python seed.py
"""
from datetime import date, datetime, timedelta

from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import (
    Usuario, Grupo, GrupoMembro, Paciente, GrupoPaciente,
    Exame, PreparoModelo, PreparoCorte, PreparoMedicamentoSuspenso, PreparoInfoGeral, PreparoAlimento,
    PreparoExameAnterior, PreparoMedicamentoMantido, Medicamento,
    Agendamento, FaqItem, normalizar_telefone,
    LicencaPagamento, garantir_meses_licenca, PlataformaConfig,
)

app = create_app()

with app.app_context():
    resetar_banco(db)

    # --- Dono da plataforma ---
    dono = Usuario(nome="Dono da Plataforma", email="dono@plataforma.com", tipo="dono")
    dono.set_senha("123456")
    db.session.add(dono)
    db.session.commit()

    # --- Grupos ---
    # Fatia 5: cada Grupo já é a própria unidade de cobrança/fiscal/escopo
    # (equivalente a uma filial de antes) - não existe mais uma Empresa
    # por cima agrupando vários deles.
    grupo_vitoria = Grupo(
        nome="Clínica Vitória",
        razao_social="Clínica Vitória Diagnósticos Ltda.",
        cnpj="12.345.678/0001-90",
        email_contato="contato@clinicavitoria.com",
        telefone="(27) 3333-4444",
        cep="29010-000",
        rua="Av. Jerônimo Monteiro",
        numero="1000",
        bairro="Centro",
        cidade="Vitória",
        uf="ES",
        inscricao_estadual="081.234.567",
        regime_tributario="Simples Nacional",
        cnae="8640-2/02",
        codigo_ibge_municipio="3205309",
        status="ativa",
        data_vencimento=datetime.utcnow().date() + timedelta(days=20),
    )
    grupo_sp = Grupo(
        nome="Clínica São Paulo",
        email_contato="contato@clinicasp.com",
        status="trial",
        data_vencimento=datetime.utcnow().date() + timedelta(days=7),
    )
    # Antes "Grupo Saúde Total" era uma empresa com 2 filiais; como agora
    # 1 Grupo = 1 unidade autônoma (decisão de negócio da Fatia 5), isso
    # vira 2 Grupos distintos com a MESMA equipe tendo vínculo ativo nos
    # dois - demonstra o caso de quem precisa escolher com qual Grupo
    # trabalhar no momento (ver escolher_clinica em routes_medico.py).
    grupo_saude_centro = Grupo(
        nome="Grupo Saúde Total - Centro",
        cidade="Vila Velha", uf="ES",
        status="ativa",
        data_vencimento=datetime.utcnow().date() + timedelta(days=25),
        valor_por_medico=150.00,
    )
    grupo_saude_praia = Grupo(
        nome="Grupo Saúde Total - Praia",
        cidade="Vila Velha", uf="ES",
        status="ativa",
        data_vencimento=datetime.utcnow().date() + timedelta(days=25),
        valor_por_medico=150.00,
    )
    db.session.add_all([grupo_vitoria, grupo_sp, grupo_saude_centro, grupo_saude_praia])
    db.session.commit()

    # --- Usuários da equipe ---
    # Permissões administrativas (pacientes/equipe/dados do grupo) não são
    # amarradas ao papel médico/secretária - nem todo grupo tem
    # secretária. No seed, damos as permissões padrão de acordo com o
    # papel (secretária = todas; médico = nenhuma).
    secretaria_vitoria = Usuario(nome="Ana Secretária", email="secretaria@clinicavitoria.com", tipo="secretaria")
    secretaria_vitoria.set_senha("123456")
    secretaria_vitoria.definir_permissoes_padrao()

    secretaria_sp = Usuario(nome="Bruna Secretária", email="secretaria@clinicasp.com", tipo="secretaria")
    secretaria_sp.set_senha("123456")
    secretaria_sp.definir_permissoes_padrao()

    # Este médico atende nos dois grupos (Vitória e São Paulo) — demonstra
    # o vínculo multi-grupo.
    medico_compartilhado = Usuario(nome="Dr. Carlos Andrade", email="medico@clinicavitoria.com", tipo="medico")
    medico_compartilhado.set_senha("123456")
    medico_compartilhado.definir_permissoes_padrao()
    # Fatia 8 (licença individual): licença ativa, vencimento confortável no
    # futuro - cenário "tudo em dia".
    medico_compartilhado.licenca_status = "ativa"
    medico_compartilhado.licenca_vencimento = date.today() + timedelta(days=180)
    medico_compartilhado.valor_licenca_mensal = 180.00

    # Uma segunda médica, só no Grupo Vitória — demonstra que cada médico
    # só cadastra/acompanha os seus próprios exames e pacientes.
    medica_vitoria2 = Usuario(nome="Dra. Fernanda Lima", email="medica2@clinicavitoria.com", tipo="medico")
    medica_vitoria2.set_senha("123456")
    medica_vitoria2.definir_permissoes_padrao()
    # Fatia 8: ainda em trial, vencendo em breve - cenário "atenção".
    medica_vitoria2.licenca_status = "trial"
    medica_vitoria2.licenca_vencimento = date.today() + timedelta(days=5)
    # Ainda em trial - valor nem foi negociado ainda (fica em branco).

    # Equipe do Grupo Saúde Total: uma secretária que administra os dois
    # grupos, e um médico que atende nos dois — pela regra de cobrança
    # "por grupo x médico", ele conta uma vez em cada cobrança.
    secretaria_grupo = Usuario(nome="Camila Rocha", email="secretaria@gruposaude.com", tipo="secretaria")
    secretaria_grupo.set_senha("123456")
    secretaria_grupo.definir_permissoes_padrao()

    medico_grupo = Usuario(nome="Dr. Eduardo Nunes", email="medico@gruposaude.com", tipo="medico")
    medico_grupo.set_senha("123456")
    medico_grupo.definir_permissoes_padrao()
    # Fatia 8: trial já vencido, ainda não regularizado - cenário
    # "pendente de pagamento" (só informativo, não bloqueia o acesso).
    medico_grupo.licenca_status = "inadimplente"
    medico_grupo.licenca_vencimento = date.today() - timedelta(days=3)
    medico_grupo.valor_licenca_mensal = 150.00
    # Restruturação de 2026-09-02: o limite de meses pra aviso de
    # inadimplência deixou de ser por médico e virou global
    # (PlataformaConfig.aviso_inadimplencia_meses, ajustado abaixo pra 1 em
    # vez do padrão de 2) - junto com o histórico de meses seguidos sem
    # pagar montado abaixo, medico_grupo já nasce em alerta na tela do dono
    # (/dono/usuarios).
    PlataformaConfig.obter().aviso_inadimplencia_meses = 1

    db.session.add_all([
        secretaria_vitoria, secretaria_sp, medico_compartilhado, medica_vitoria2,
        secretaria_grupo, medico_grupo,
    ])
    db.session.commit()

    # --- Calendário de pagamento (Fatia 8) ---
    # Gera os meses desde o cadastro de cada médico (todos "hoje" no seed,
    # então normalmente só o mês atual) e marca alguns como pagos, pra
    # demonstrar os dois estados na tela "Minha licença"/no painel do dono.
    for medico in (medico_compartilhado, medica_vitoria2, medico_grupo):
        garantir_meses_licenca(medico)
    db.session.commit()

    mes_atual = date.today().replace(day=1)
    LicencaPagamento.query.filter_by(usuario_id=medico_compartilhado.id, mes=mes_atual).update(
        {"pago": True, "pago_em": datetime.utcnow()}
    )
    # medica_vitoria2 (trial) e medico_grupo (inadimplente) ficam com o mês
    # atual em aberto, coerente com o status de cada um.
    db.session.commit()

    # Fatia 8 (aviso de inadimplência): medico_grupo já nasce com 2 meses
    # seguidos sem pagar (mês atual + o anterior), pra demonstrar o destaque
    # de alerta em /dono/usuarios logo de cara (o seed normalmente só cria
    # todo mundo "hoje", então sem isso nunca haveria mais de um mês de
    # histórico pra nenhum médico).
    mes_anterior = (mes_atual.replace(day=1) - timedelta(days=1)).replace(day=1)
    db.session.add(LicencaPagamento(
        usuario_id=medico_grupo.id, mes=mes_anterior, pago=False,
        valor=medico_grupo.valor_licenca_mensal,
    ))
    db.session.commit()

    # --- Vínculos (GrupoMembro) ---
    # O "dono" de cada grupo é quem, no fluxo real, teria se cadastrado
    # primeiro (ver routes_auth.py:cadastro) - aqui só precisa existir
    # um por grupo (invariante de Grupo).
    db.session.add_all([
        GrupoMembro(grupo_id=grupo_vitoria.id, usuario_id=secretaria_vitoria.id, papel="dono", ativo=True),
        GrupoMembro(grupo_id=grupo_vitoria.id, usuario_id=medico_compartilhado.id, papel="administrador", ativo=True),
        GrupoMembro(grupo_id=grupo_vitoria.id, usuario_id=medica_vitoria2.id, papel="membro", ativo=True),

        GrupoMembro(grupo_id=grupo_sp.id, usuario_id=secretaria_sp.id, papel="dono", ativo=True),
        GrupoMembro(grupo_id=grupo_sp.id, usuario_id=medico_compartilhado.id, papel="membro", ativo=True),

        GrupoMembro(grupo_id=grupo_saude_centro.id, usuario_id=secretaria_grupo.id, papel="dono", ativo=True),
        GrupoMembro(grupo_id=grupo_saude_centro.id, usuario_id=medico_grupo.id, papel="administrador", ativo=True),

        GrupoMembro(grupo_id=grupo_saude_praia.id, usuario_id=secretaria_grupo.id, papel="dono", ativo=True),
        GrupoMembro(grupo_id=grupo_saude_praia.id, usuario_id=medico_grupo.id, papel="administrador", ativo=True),
    ])
    db.session.commit()

    # --- Catálogo de medicamentos (compartilhado pela plataforma) ---
    med_ozempic = Medicamento(nome="Ozempic, Mounjaro, Trulicity ou similares", dias_padrao_suspensao=14, categoria="medicamento para emagrecimento")
    med_xarelto = Medicamento(nome="Xarelto, Eliquis ou similares", dias_padrao_suspensao=3, categoria="medicamento anticoagulante")
    med_clopidogrel = Medicamento(nome="Clopidogrel, Marevan ou similares", dias_padrao_suspensao=7, categoria="medicamento antiplaquetário")
    db.session.add_all([med_ozempic, med_xarelto, med_clopidogrel])
    db.session.flush()

    # --- Modelos de preparo (reaproveitáveis entre exames do mesmo grupo)
    # e exames que os usam ---
    def criar_modelo(
        grupo_id, nome, instrucoes, cortes=None, medicamentos=None, observacoes_medicamentos=None,
        medicamentos_mantidos=None, informacoes_gerais=None, alimentos=None, exames_anteriores=None,
    ):
        modelo = PreparoModelo(
            grupo_id=grupo_id, nome=nome, instrucoes=instrucoes,
            observacoes_medicamentos=observacoes_medicamentos,
        )
        db.session.add(modelo)
        db.session.flush()
        for descricao, horas_antes in (cortes or []):
            db.session.add(PreparoCorte(preparo_modelo_id=modelo.id, descricao=descricao, horas_antes=horas_antes))
        for medicamento, dias_antes in (medicamentos or []):
            db.session.add(PreparoMedicamentoSuspenso(
                preparo_modelo_id=modelo.id, medicamento_id=medicamento.id, dias_antes=dias_antes,
            ))
        for nome_mantido, observacao in (medicamentos_mantidos or []):
            db.session.add(PreparoMedicamentoMantido(
                preparo_modelo_id=modelo.id, nome=nome_mantido, observacao=observacao,
            ))
        for texto in (informacoes_gerais or []):
            db.session.add(PreparoInfoGeral(preparo_modelo_id=modelo.id, texto=texto))
        for nome_alimento, permitido, horas_antes in (alimentos or []):
            db.session.add(PreparoAlimento(
                preparo_modelo_id=modelo.id, nome=nome_alimento, permitido=permitido, horas_antes=horas_antes,
            ))
        for nome_exame, dias_antes in (exames_anteriores or []):
            db.session.add(PreparoExameAnterior(
                preparo_modelo_id=modelo.id, nome=nome_exame, dias_antes=dias_antes,
            ))
        db.session.flush()
        return modelo

    def criar_exame(grupo_id, medico_id, nome, descricao, modelo):
        exame = Exame(
            grupo_id=grupo_id, medico_id=medico_id, nome=nome, descricao=descricao, preparo_modelo=modelo,
        )
        db.session.add(exame)
        db.session.flush()
        return exame

    modelo_colonoscopia_vitoria = criar_modelo(
        grupo_vitoria.id, "Colonoscopia - padrão Vitória",
        "3 dias antes: evite alimentos com sementes, grãos integrais e vegetais crus.\n"
        "1 dia antes: dieta líquida (água, chás claros, caldo coado). Tome o laxante conforme prescrito.\n"
        "Evite alimentos ricos em fibra, como batata com casca, milho e feijão, nos 3 dias anteriores.",
        cortes=[("Alimentos sólidos", 12), ("Líquidos claros", 2)],
        medicamentos=[(med_xarelto, 3), (med_clopidogrel, 7)],
        observacoes_medicamentos="Não é necessário suspender AAS, Somalgin ou Aspirina.",
        medicamentos_mantidos=[
            ("AAS", "medicamento analgésico — não precisa suspender"),
            ("Somalgin", "medicamento analgésico — não precisa suspender"),
            ("Aspirina", "medicamento analgésico — não precisa suspender"),
        ],
        alimentos=[
            ("Leite e derivados", False, 12),
            ("Feijão e outras leguminosas", False, 12),
            ("Milho", False, 12),
            ("Amendoim", False, 12),
            ("Frutas com sementes e casca", False, 12),
            ("Pão integral", False, 12),
            ("Água de coco", True, None),
            ("Chá claro", True, None),
            ("Caldo de legumes coado", True, None),
            ("Gelatina sem cor vermelha ou roxa", True, None),
        ],
    )
    colonoscopia_vitoria = criar_exame(
        grupo_vitoria.id, medico_compartilhado.id, "Colonoscopia", "Exame do intestino grosso",
        modelo_colonoscopia_vitoria,
    )
    # A Colonoscopia também pode ser atendida pela Dra. Fernanda — demonstra
    # a associação de mais de um médico ao mesmo exame.
    colonoscopia_vitoria.medicos_extra = [medica_vitoria2]

    # Um mesmo modelo de preparo (teste de hidrogênio) reaproveitado por 3
    # exames diferentes — cada substrato precisa ser agendado num dia
    # separado, mas o preparo é idêntico, então não precisa duplicar o
    # cadastro do preparo em cada exame.
    modelo_hidrogenio_vitoria = criar_modelo(
        grupo_vitoria.id, "Teste de Hidrogênio/Metano Expirado - padrão",
        "Deverá comparecer ao local do exame trazendo o pedido médico.\n"
        "Não deve ter realizado colonoscopia ou lavagens intestinais nas 4 semanas anteriores ao exame.\n"
        "Não utilizar laxantes e procinéticos na semana que antecede o exame.\n"
        "Não utilizar opióides na véspera do exame.\n"
        "A duração do exame é de 2 a 3 horas, com coletas de ar expirado em intervalos regulares.",
        cortes=[("Jejum total (sólidos e líquidos)", 12)],
        medicamentos=[(med_ozempic, 14)],
        informacoes_gerais=[
            "Realizar escovação dental no dia do exame, com uso da pasta de dente de costume.",
            "Não utilizar enxaguante bucal com álcool no dia do exame.",
            "Não é permitido fumar, mascar chiclete ou praticar atividade física antes do exame.",
            "A dieta no dia anterior ao exame deve ser não fermentativa.",
        ],
        exames_anteriores=[
            ("Colonoscopia", 28),
            ("Lavagens intestinais", 28),
        ],
    )
    hidrogenio_lactose_vitoria = criar_exame(
        grupo_vitoria.id, medico_compartilhado.id, "Teste do Hidrogênio - Lactose",
        "Investigação de intolerância à lactose", modelo_hidrogenio_vitoria,
    )
    hidrogenio_frutose_vitoria = criar_exame(
        grupo_vitoria.id, medico_compartilhado.id, "Teste do Hidrogênio - Frutose",
        "Investigação de intolerância à frutose", modelo_hidrogenio_vitoria,
    )
    hidrogenio_sibo_vitoria = criar_exame(
        grupo_vitoria.id, medico_compartilhado.id, "Teste do Hidrogênio - Glicose (SIBO)",
        "Investigação de SIBO/IMO", modelo_hidrogenio_vitoria,
    )

    modelo_glicemia_vitoria = criar_modelo(
        grupo_vitoria.id, "Glicemia de jejum - padrão",
        "Não é permitido comer nenhum alimento, incluindo frutas, café com açúcar ou balas.\n"
        "Se usar insulina ou outro medicamento, converse com o médico antes de suspender o uso.",
        cortes=[("Jejum total (sólidos e líquidos)", 8)],
    )
    glicemia_vitoria = criar_exame(
        grupo_vitoria.id, medico_compartilhado.id, "Glicemia de jejum", "Exame de sangue para medir glicose",
        modelo_glicemia_vitoria,
    )

    modelo_hemograma_vitoria = criar_modelo(
        grupo_vitoria.id, "Hemograma completo - padrão",
        "Não é necessário jejum para este exame na maioria dos casos.\n"
        "Beba água normalmente e evite exercícios físicos intensos nas 24h anteriores.\n"
        "Se estiver tomando algum medicamento contínuo, informe a secretaria antes do exame.",
    )
    hemograma_vitoria = criar_exame(
        grupo_vitoria.id, medica_vitoria2.id, "Hemograma completo", "Exame de sangue de rotina",
        modelo_hemograma_vitoria,
    )

    modelo_colonoscopia_sp = criar_modelo(
        grupo_sp.id, "Colonoscopia - padrão São Paulo",
        "Siga a dieta de preparo intestinal conforme orientado pela equipe da Clínica São Paulo.",
        cortes=[("Jejum total (sólidos e líquidos)", 12)],
    )
    colonoscopia_sp = criar_exame(
        grupo_sp.id, medico_compartilhado.id, "Colonoscopia", "Exame do intestino grosso",
        modelo_colonoscopia_sp,
    )

    db.session.commit()

    # --- Pacientes (um por grupo, para mostrar isolamento) ---
    # Pacientes não têm senha — o acesso é feito informando telefone e data
    # de nascimento (ver auth.login_paciente). O e-mail é só um dado de
    # contato opcional, não é mais usado para login do paciente.
    telefone_joao = normalizar_telefone("(27) 99999-0000")
    usuario_joao = Usuario(nome="João Pereira", telefone=telefone_joao, tipo="paciente")
    db.session.add(usuario_joao)
    db.session.flush()

    joao = Paciente(
        usuario_id=usuario_joao.id,
        nome="João Pereira",
        cpf="123.456.789-00",
        data_nascimento=date(1985, 4, 12),
        telefone=telefone_joao,
        email="joao@paciente.com",
    )
    db.session.add(joao)

    telefone_maria = normalizar_telefone("(11) 98888-0000")
    usuario_maria = Usuario(nome="Maria Silva", telefone=telefone_maria, tipo="paciente")
    db.session.add(usuario_maria)
    db.session.flush()

    maria = Paciente(
        usuario_id=usuario_maria.id,
        nome="Maria Silva",
        cpf="987.654.321-00",
        data_nascimento=date(1990, 9, 3),
        telefone=telefone_maria,
        email="maria@paciente.com",
    )
    db.session.add(maria)

    # Paciente exclusivo da Dra. Fernanda, no Grupo Vitória — usado para
    # demonstrar que o Dr. Carlos não vê os pacientes de outro médico.
    telefone_pedro = normalizar_telefone("(27) 99999-1111")
    usuario_pedro = Usuario(nome="Pedro Souza", telefone=telefone_pedro, tipo="paciente")
    db.session.add(usuario_pedro)
    db.session.flush()

    pedro = Paciente(
        usuario_id=usuario_pedro.id,
        nome="Pedro Souza",
        cpf="111.222.333-00",
        data_nascimento=date(1978, 12, 25),
        telefone=telefone_pedro,
        email="pedro@paciente.com",
    )
    db.session.add(pedro)
    db.session.commit()

    # A associação de fato (visibilidade "é paciente deste grupo",
    # ver medico._filtro_pacientes_da_empresa) é 100% via GrupoPaciente -
    # paciente é uma identidade global (Fatia 5).
    db.session.add(GrupoPaciente(grupo_id=grupo_vitoria.id, paciente_id=joao.id))
    db.session.add(GrupoPaciente(grupo_id=grupo_sp.id, paciente_id=maria.id))
    db.session.add(GrupoPaciente(grupo_id=grupo_vitoria.id, paciente_id=pedro.id))
    db.session.commit()

    # --- Agendamentos (cada um tem um médico responsável, herdado do exame) ---
    db.session.add(Agendamento(
        grupo_id=grupo_vitoria.id,
        paciente_id=joao.id,
        exame_id=colonoscopia_vitoria.id,
        medico_id=colonoscopia_vitoria.medico_id,
        # Data fixa (não relativa a "agora") — evita colidir de forma
        # imprevisível com outros agendamentos de teste do mesmo
        # paciente/exame criados com datas fixas (ver test_smoke.py), o
        # que já causou falhas de teste dependentes do horário real em
        # que o seed é executado.
        data_hora=datetime(2026, 8, 6, 10, 0),
    ))
    db.session.add(Agendamento(
        grupo_id=grupo_vitoria.id,
        paciente_id=joao.id,
        exame_id=glicemia_vitoria.id,
        medico_id=glicemia_vitoria.medico_id,
        data_hora=datetime.utcnow() - timedelta(days=10),
        encerrado_em=datetime.utcnow() - timedelta(days=10),
    ))
    db.session.add(Agendamento(
        grupo_id=grupo_vitoria.id,
        paciente_id=pedro.id,
        exame_id=hemograma_vitoria.id,
        medico_id=hemograma_vitoria.medico_id,
        data_hora=datetime.utcnow() + timedelta(days=2),
    ))
    db.session.add(Agendamento(
        grupo_id=grupo_sp.id,
        paciente_id=maria.id,
        exame_id=colonoscopia_sp.id,
        medico_id=colonoscopia_sp.medico_id,
        data_hora=datetime.utcnow() + timedelta(days=3),
    ))
    db.session.commit()

    # --- Base inicial de FAQ (por grupo) ---
    db.session.add_all([
        FaqItem(
            grupo_id=grupo_vitoria.id,
            exame_id=colonoscopia_vitoria.id,
            pergunta="Posso comer batata antes da colonoscopia?",
            resposta="Não. Batata (principalmente com casca) tem fibra e pode prejudicar a limpeza do intestino. Evite nos 3 dias antes do exame.",
            criado_por="Ana Secretária",
        ),
        FaqItem(
            grupo_id=grupo_vitoria.id,
            exame_id=colonoscopia_vitoria.id,
            pergunta="Posso beber água durante o jejum?",
            resposta="Sim, água pura é permitida até 2 horas antes do exame.",
            criado_por="Ana Secretária",
        ),
        FaqItem(
            grupo_id=grupo_vitoria.id,
            exame_id=glicemia_vitoria.id,
            pergunta="Posso tomar café antes da glicemia de jejum?",
            resposta="Somente café sem açúcar e sem leite, em pequena quantidade. O ideal é evitar qualquer alimento ou bebida calórica durante o jejum.",
            criado_por="Dr. Carlos Andrade",
        ),
        FaqItem(
            grupo_id=grupo_vitoria.id,
            exame_id=None,
            pergunta="Posso tomar meus medicamentos normalmente?",
            resposta="Na maioria dos casos sim, com um pouco de água. Mas avise sempre a secretaria sobre quais medicamentos você usa, pois alguns exames exigem ajustes.",
            criado_por="Dr. Carlos Andrade",
        ),
        FaqItem(
            grupo_id=grupo_sp.id,
            exame_id=colonoscopia_sp.id,
            pergunta="Posso comer normalmente até a véspera do exame?",
            resposta="Não, siga a dieta de preparo intestinal indicada pela equipe a partir de 3 dias antes.",
            criado_por="Bruna Secretária",
        ),
    ])
    db.session.commit()

    print("Banco de dados populado com sucesso!")
    print()
    print("Contas de demonstração:")
    print("  Dono da plataforma: dono@plataforma.com / 123456")
    print()
    print("  Grupo 'Clínica Vitória' (status: ativa):")
    print("    Secretária (dono do grupo): secretaria@clinicavitoria.com / 123456")
    print("    Médico (também atua no grupo Clínica São Paulo): medico@clinicavitoria.com / 123456")
    print("    Médica (só neste grupo): medica2@clinicavitoria.com / 123456")
    print("    Paciente do Dr. Carlos: telefone (27) 99999-0000 / nascimento 12/04/1985")
    print("    Paciente da Dra. Fernanda: telefone (27) 99999-1111 / nascimento 25/12/1978")
    print()
    print("  Grupo 'Clínica São Paulo' (status: trial):")
    print("    Secretária (dono do grupo): secretaria@clinicasp.com / 123456")
    print("    Paciente: telefone (11) 98888-0000 / nascimento 03/09/1990")
    print()
    print("  Grupos 'Grupo Saúde Total - Centro' e '- Praia' (status: ativa, R$150/médico cada):")
    print("    Secretária (dono dos dois grupos): secretaria@gruposaude.com / 123456")
    print("    Médico (administrador nos dois grupos, conta 1x em cada cobrança): medico@gruposaude.com / 123456")
