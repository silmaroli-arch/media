"""
Ponto de entrada esperado pelo AWS Elastic Beanstalk (plataforma Python).

O Elastic Beanstalk procura, por padrão, um arquivo "application.py" na raiz
do projeto com uma variável "application" contendo o app WSGI (no nosso
caso, o app Flask). Por isso este arquivo existe separado do run.py, que é
usado para rodar localmente durante o desenvolvimento.
"""
from app import create_app

application = create_app()

if __name__ == "__main__":
    application.run()
