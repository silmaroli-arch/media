"""Lista inicial de TIPOS DE EXAME que exigem preparo (pedido do Silvan,
2026-09-29) - alimenta o dropdown "Tipo de exame" do cadastro de preparo
(ver PreparoModelo.tipo_exame_id) e, nas fatias seguintes, a base de
conhecimento compartilhada (a "terceira IA") e a visibilidade por
especialidade do médico.

Cada item é (nome, especialidades) - `especialidades` é uma lista de nomes
de especialidade médica que costumam solicitar/realizar o exame (gravada
como texto separado por vírgula em TipoExame.especialidades). A lista é
só o PONTO DE PARTIDA: o dono da plataforma pode adicionar, renomear e
inativar tipos em /dono/tipos-exame - por isso `semear_tipos_exame` só
INSERE o que falta (comparando pelo nome, sem diferenciar maiúsculas),
nunca altera nem reativa um tipo que o dono já mexeu.

Cuidado ao editar esta lista: não use ";" dentro de comentários nem de
textos que possam ir parar em migrar_banco.py (o parser dele quebra o SQL
por ";")."""

GASTRO = "Gastroenterologia"
COLOPROCTO = "Coloproctologia"
CARDIO = "Cardiologia"
IMAGEM = "Radiologia e diagnóstico por imagem"
GINECO = "Ginecologia e obstetrícia"
URO = "Urologia"
PNEUMO = "Pneumologia"
NEURO = "Neurologia"
OFTALMO = "Oftalmologia"
OTORRINO = "Otorrinolaringologia"
ENDOCRINO = "Endocrinologia"
NUCLEAR = "Medicina nuclear"
LAB = "Patologia clínica e laboratório"
CLINICA = "Clínica médica"
MAMA = "Mastologia"

TIPOS_EXAME_PADRAO = [
    # --- Endoscopia e aparelho digestivo ---
    ("Colonoscopia", [GASTRO, COLOPROCTO]),
    ("Retossigmoidoscopia", [GASTRO, COLOPROCTO]),
    ("Endoscopia digestiva alta", [GASTRO]),
    ("Enteroscopia", [GASTRO]),
    ("Cápsula endoscópica", [GASTRO]),
    ("Ecoendoscopia", [GASTRO]),
    ("CPRE (colangiopancreatografia retrógrada endoscópica)", [GASTRO]),
    ("Manometria esofágica", [GASTRO]),
    ("pHmetria e impedanciometria esofágica", [GASTRO]),
    ("Manometria anorretal", [GASTRO, COLOPROCTO]),
    ("Defecografia", [COLOPROCTO, IMAGEM]),
    ("Teste respiratório (hidrogênio expirado: lactose, frutose, SIBO)", [GASTRO]),
    ("Teste respiratório da ureia (H. pylori)", [GASTRO]),
    ("Colonografia por tomografia (colonoscopia virtual)", [IMAGEM, GASTRO, COLOPROCTO]),
    # --- Ultrassonografia ---
    ("Ultrassonografia de abdome total", [IMAGEM, GASTRO]),
    ("Ultrassonografia de abdome superior", [IMAGEM, GASTRO]),
    ("Ultrassonografia pélvica (bexiga cheia)", [IMAGEM, GINECO, URO]),
    ("Ultrassonografia transvaginal", [IMAGEM, GINECO]),
    ("Ultrassonografia de vias urinárias e próstata", [IMAGEM, URO]),
    ("Ecocardiograma transesofágico", [CARDIO]),
    # --- Tomografia e ressonância ---
    ("Tomografia computadorizada com contraste", [IMAGEM]),
    ("Angiotomografia", [IMAGEM, CARDIO]),
    ("Urotomografia", [IMAGEM, URO]),
    ("Ressonância magnética com contraste", [IMAGEM]),
    ("Ressonância magnética com sedação", [IMAGEM]),
    ("Enterografia por tomografia ou ressonância", [IMAGEM, GASTRO]),
    ("Colangiorressonância", [IMAGEM, GASTRO]),
    # --- Radiografias contrastadas ---
    ("Radiografia contrastada do esôfago, estômago e duodeno", [IMAGEM, GASTRO]),
    ("Trânsito intestinal", [IMAGEM, GASTRO]),
    ("Enema opaco", [IMAGEM, COLOPROCTO]),
    ("Urografia excretora", [IMAGEM, URO]),
    ("Uretrocistografia miccional", [IMAGEM, URO]),
    ("Histerossalpingografia", [IMAGEM, GINECO]),
    # --- Medicina nuclear ---
    ("Cintilografia do miocárdio", [NUCLEAR, CARDIO]),
    ("Cintilografia da tireoide", [NUCLEAR, ENDOCRINO]),
    ("Cintilografia óssea", [NUCLEAR]),
    ("Cintilografia renal", [NUCLEAR, URO]),
    ("PET-CT", [NUCLEAR]),
    ("Densitometria óssea", [IMAGEM, ENDOCRINO]),
    # --- Cardiologia ---
    ("Teste ergométrico", [CARDIO]),
    ("Ecocardiograma de estresse", [CARDIO]),
    ("Cateterismo cardíaco", [CARDIO]),
    ("Teste de inclinação (tilt test)", [CARDIO]),
    # --- Laboratório ---
    ("Exames de sangue com jejum (glicemia, perfil lipídico e outros)", [LAB, CLINICA]),
    ("Curva glicêmica (TOTG)", [LAB, ENDOCRINO]),
    ("Dosagens hormonais e teste de estímulo", [LAB, ENDOCRINO]),
    ("PSA (antígeno prostático)", [LAB, URO]),
    ("Coleta de urina de 24 horas", [LAB, URO, CLINICA]),
    ("Urina tipo 1 e urocultura", [LAB, URO, CLINICA]),
    ("Parasitológico de fezes", [LAB, GASTRO]),
    ("Pesquisa de sangue oculto e calprotectina fecal", [LAB, GASTRO]),
    ("Espermograma", [LAB, URO]),
    # --- Ginecologia e urologia ---
    ("Colposcopia", [GINECO]),
    ("Histeroscopia", [GINECO]),
    ("Citologia oncótica (Papanicolau)", [GINECO]),
    ("Urodinâmica", [URO]),
    ("Cistoscopia", [URO]),
    ("Biópsia de próstata", [URO]),
    ("Biópsia de mama guiada", [MAMA, IMAGEM]),
    # --- Pneumologia, neurologia e sono ---
    ("Broncoscopia", [PNEUMO]),
    ("Espirometria", [PNEUMO]),
    ("Polissonografia", [PNEUMO, NEURO]),
    ("Eletroencefalograma", [NEURO]),
    # --- Oftalmologia e otorrino ---
    ("Mapeamento de retina e angiofluoresceinografia", [OFTALMO]),
    ("Audiometria e BERA", [OTORRINO]),
    ("Nasofibrolaringoscopia", [OTORRINO]),
]


def semear_tipos_exame(db, TipoExame):
    """Insere em `tipos_exame` os tipos da lista acima que ainda não
    existem (comparação pelo nome, sem diferenciar maiúsculas). Idempotente e
    NÃO destrutivo: nunca altera nem reativa um tipo já existente - o dono
    pode ter renomeado, editado as especialidades ou inativado de propósito.
    Não faz commit dentro de um loop: um commit só, no final. Devolve
    quantos tipos novos foram criados."""
    existentes = {t.nome.strip().lower() for t in TipoExame.query.all()}
    criados = 0
    for ordem, (nome, especialidades) in enumerate(TIPOS_EXAME_PADRAO):
        if nome.strip().lower() in existentes:
            continue
        db.session.add(TipoExame(
            nome=nome, especialidades=", ".join(especialidades), ativo=True, ordem=ordem,
        ))
        existentes.add(nome.strip().lower())
        criados += 1
    if criados:
        db.session.commit()
    return criados
