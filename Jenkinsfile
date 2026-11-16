// SunOps: CI/CD for a data pipeline.
//
//   Build image ─▶ Lint · Unit tests · Contract check ─▶ Data Canary ─▶ (main) Plan ─▶ Approval ─▶ Deploy to AWS
//
// The Data Canary runs `main` and this branch on the same golden sample and diffs the Gold
// tables. A change that passes every unit test but silently moves the numbers is blocked here.
pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '30'))
    timeout(time: 45, unit: 'MINUTES')
  }

  parameters {
    booleanParam(name: 'FORCE_DEPLOY', defaultValue: false, description: 'Deploy even when not on main (hotfix)')
    string(name: 'AWS_REGION', defaultValue: 'eu-west-3', description: 'AWS region (Paris is the closest to Morocco)')
  }

  environment {
    IMAGE      = "sunops-pipeline:${env.BUILD_NUMBER}"
    CI_NET     = "sunops-ci-${env.BUILD_NUMBER}"
    CI_PG      = "sunops-ci-pg-${env.BUILD_NUMBER}"
    PG_ENV     = "-e PGHOST=sunops-ci-pg-${env.BUILD_NUMBER} -e PGUSER=sunops -e PGPASSWORD=sunops -e PGDATABASE=sunops " +
                 "-e SUNOPS_PG_DSN=postgresql+psycopg2://sunops:sunops@sunops-ci-pg-${env.BUILD_NUMBER}:5432/sunops"
  }

  stages {
    stage('Build image') {
      steps {
        sh 'docker build -t "$IMAGE" .'
      }
    }

    stage('Quality gates') {
      parallel {
        stage('Lint') {
          steps {
            script { docker.image(env.IMAGE).inside { sh 'ruff check sunops spark_jobs tests airflow' } }
          }
        }
        stage('Unit tests') {
          steps {
            script {
              docker.image(env.IMAGE).inside {
                sh 'mkdir -p reports && pytest -q --junitxml=reports/junit.xml --cov=sunops --cov-report=xml:reports/coverage.xml'
              }
            }
          }
        }
        stage('Data contract') {
          steps {
            script {
              docker.image(env.IMAGE).inside {
                sh '''
                  mkdir -p reports
                  git fetch --quiet origin main || true
                  git show origin/main:contracts/telemetry.yml > reports/telemetry.main.yml 2>/dev/null || : > reports/telemetry.main.yml
                  python -m sunops.contracts compat reports/telemetry.main.yml contracts/telemetry.yml
                '''
              }
            }
          }
        }
      }
    }

    stage('Data Canary') {
      steps {
        sh '''
          docker network create "$CI_NET"
          docker run -d --name "$CI_PG" --network "$CI_NET" \
            -e POSTGRES_USER=sunops -e POSTGRES_PASSWORD=sunops -e POSTGRES_DB=sunops postgres:16-alpine
        '''
        script {
          docker.image(env.IMAGE).inside("--network ${env.CI_NET} ${env.PG_ENV}") {
            sh 'git fetch --quiet origin main || true; bash scripts/canary.sh'
          }
        }
      }
      post {
        always {
          publishHTML(target: [reportDir: 'reports/canary', reportFiles: 'index.html', reportName: 'Data Canary',
                               keepAll: true, alwaysLinkToLastBuild: true, allowMissing: true])
          script {
            if (fileExists('reports/canary/canary.json')) {
              def report = readJSON file: 'reports/canary/canary.json'
              currentBuild.description = "Canary ${report.verdict} · ${report.score}/100"
            }
          }
        }
      }
    }

    stage('Terraform plan') {
      when { anyOf { branch 'main'; expression { params.FORCE_DEPLOY } } }
      steps {
        withCredentials([[$class: 'AmazonWebServicesCredentialsBinding', credentialsId: 'aws-sunops']]) {
          script {
            docker.image('hashicorp/terraform:1.9').inside('--entrypoint=') {
              sh '''
                cd infra/terraform
                terraform init -input=false
                terraform plan -input=false -var "region=${AWS_REGION}" -var "image_tag=${BUILD_NUMBER}" -out=tfplan
                terraform show -no-color tfplan > ../../reports/tfplan.txt
              '''
            }
          }
        }
      }
    }

    stage('Approval') {
      when { anyOf { branch 'main'; expression { params.FORCE_DEPLOY } } }
      steps {
        timeout(time: 2, unit: 'HOURS') {
          input message: 'Canary is green and the Terraform plan is archived. Deploy to AWS?', ok: 'Deploy'
        }
      }
    }

    stage('Deploy to AWS') {
      when { anyOf { branch 'main'; expression { params.FORCE_DEPLOY } } }
      steps {
        withCredentials([[$class: 'AmazonWebServicesCredentialsBinding', credentialsId: 'aws-sunops']]) {
          script {
            docker.image('hashicorp/terraform:1.9').inside('--entrypoint=') {
              sh 'cd infra/terraform && terraform apply -input=false tfplan && terraform output -json > ../../reports/tf-outputs.json'
            }
          }
          sh 'bash scripts/deploy_aws.sh reports/tf-outputs.json'
        }
      }
    }
  }

  post {
    always {
      junit allowEmptyResults: true, testResults: 'reports/junit.xml'
      archiveArtifacts allowEmptyArchive: true, artifacts: 'reports/**'
      sh 'docker rm -f "$CI_PG" >/dev/null 2>&1 || true; docker network rm "$CI_NET" >/dev/null 2>&1 || true'
    }
    failure {
      echo 'Pipeline blocked. Open the "Data Canary" report on this build to see which numbers moved.'
    }
  }
}
