// Generated from cases.json for the sandbox plugin runner, which cannot read JSON files.
// tests/test_fleet_watch.py fails if the two drift apart.
export type TestCase = { command: string; rule: string | null };

export const cases: TestCase[] = [
  {
    "command": "git diff --name-only | xargs git checkout --",
    "rule": "git-history"
  },
  {
    "command": "for f in a b; do git checkout -- \"$f\"; done",
    "rule": "git-history"
  },
  {
    "command": "(git stash)",
    "rule": "git-history"
  },
  {
    "command": "( git stash )",
    "rule": "git-history"
  },
  {
    "command": "timeout 30 git push",
    "rule": "git-history"
  },
  {
    "command": "nice git stash",
    "rule": "git-history"
  },
  {
    "command": "nice -n 10 git push",
    "rule": "git-history"
  },
  {
    "command": "ionice -c 2 git push",
    "rule": "git-history"
  },
  {
    "command": "stdbuf -oL git push",
    "rule": "git-history"
  },
  {
    "command": "doas git push",
    "rule": "git-history"
  },
  {
    "command": "sudo -u root git push",
    "rule": "git-history"
  },
  {
    "command": "sudo -E git reset --hard",
    "rule": "git-history"
  },
  {
    "command": "env FOO=1 git commit -m msg",
    "rule": "git-history"
  },
  {
    "command": "bundle exec rails db:migrate",
    "rule": "migration"
  },
  {
    "command": "uv run alembic upgrade head",
    "rule": "migration"
  },
  {
    "command": "poetry run python manage.py migrate",
    "rule": "migration"
  },
  {
    "command": "pipenv run alembic upgrade head",
    "rule": "migration"
  },
  {
    "command": "npx sequelize-cli db:migrate",
    "rule": "migration"
  },
  {
    "command": "npx -y prisma migrate deploy",
    "rule": "migration"
  },
  {
    "command": "npm exec prisma migrate deploy",
    "rule": "migration"
  },
  {
    "command": "pnpm exec prisma migrate deploy",
    "rule": "migration"
  },
  {
    "command": "pnpm dlx prisma migrate deploy",
    "rule": "migration"
  },
  {
    "command": "yarn dlx prisma migrate deploy",
    "rule": "migration"
  },
  {
    "command": "bunx prisma migrate deploy",
    "rule": "migration"
  },
  {
    "command": "./vendor/bin/sail artisan migrate",
    "rule": "migration"
  },
  {
    "command": "sail artisan migrate",
    "rule": "migration"
  },
  {
    "command": "docker compose exec app php artisan migrate",
    "rule": "migration"
  },
  {
    "command": "docker-compose exec app php artisan migrate",
    "rule": "migration"
  },
  {
    "command": "docker exec -it app php artisan migrate",
    "rule": "migration"
  },
  {
    "command": "docker compose exec db psql -c \"DROP TABLE users\"",
    "rule": "db-write"
  },
  {
    "command": "docker run --rm db psql -c \"DROP TABLE users\"",
    "rule": "db-write"
  },
  {
    "command": "kubectl exec -it pod -- git push",
    "rule": "git-history"
  },
  {
    "command": "find . -name \"*.bak\" -exec rm -rf {} +",
    "rule": null
  },
  {
    "command": "find . -exec rm -rf / \\;",
    "rule": "rm-dangerous"
  },
  {
    "command": "find . -name \"*.txt\" -exec git checkout {} \\;",
    "rule": "git-history"
  },
  {
    "command": "git \\\n push",
    "rule": "git-history"
  },
  {
    "command": "git commit -m \"fix\"",
    "rule": "git-history"
  },
  {
    "command": "git push origin main",
    "rule": "git-history"
  },
  {
    "command": "git pull",
    "rule": "git-history"
  },
  {
    "command": "git pull origin main",
    "rule": "git-history"
  },
  {
    "command": "git pull --rebase",
    "rule": "git-history"
  },
  {
    "command": "git merge feature",
    "rule": "git-history"
  },
  {
    "command": "git rebase main",
    "rule": "git-history"
  },
  {
    "command": "git reset --hard HEAD~1",
    "rule": "git-history"
  },
  {
    "command": "git restore .",
    "rule": "git-history"
  },
  {
    "command": "git checkout main",
    "rule": "git-history"
  },
  {
    "command": "git switch main",
    "rule": "git-history"
  },
  {
    "command": "git clean -fd",
    "rule": "git-history"
  },
  {
    "command": "git cherry-pick abc1234",
    "rule": "git-history"
  },
  {
    "command": "git revert HEAD",
    "rule": "git-history"
  },
  {
    "command": "git am patch.patch",
    "rule": "git-history"
  },
  {
    "command": "git tag v1.0.0",
    "rule": "git-history"
  },
  {
    "command": "git tag -d v1.0.0",
    "rule": "git-history"
  },
  {
    "command": "git tag -D v1.0.0",
    "rule": "git-history"
  },
  {
    "command": "git tag -f v1.0.0",
    "rule": "git-history"
  },
  {
    "command": "git tag --delete v1.0.0",
    "rule": "git-history"
  },
  {
    "command": "git tag --force v1.0.0",
    "rule": "git-history"
  },
  {
    "command": "git stash",
    "rule": "git-history"
  },
  {
    "command": "git stash pop",
    "rule": "git-history"
  },
  {
    "command": "git stash drop",
    "rule": "git-history"
  },
  {
    "command": "git stash apply",
    "rule": "git-history"
  },
  {
    "command": "git branch -d feat",
    "rule": "git-history"
  },
  {
    "command": "git branch -D feat",
    "rule": "git-history"
  },
  {
    "command": "git branch -m old new",
    "rule": "git-history"
  },
  {
    "command": "git branch -M old new",
    "rule": "git-history"
  },
  {
    "command": "git branch -f feat",
    "rule": "git-history"
  },
  {
    "command": "git branch --force feat",
    "rule": "git-history"
  },
  {
    "command": "git branch --delete feat",
    "rule": "git-history"
  },
  {
    "command": "git branch --move old new",
    "rule": "git-history"
  },
  {
    "command": "git worktree remove tmp",
    "rule": "git-history"
  },
  {
    "command": "git worktree prune",
    "rule": "git-history"
  },
  {
    "command": "git update-ref refs/heads/main HEAD",
    "rule": "git-history"
  },
  {
    "command": "git filter-branch --tree-filter \"rm -f p\" HEAD",
    "rule": "git-history"
  },
  {
    "command": "git bisect start",
    "rule": "git-history"
  },
  {
    "command": "git bisect reset",
    "rule": "git-history"
  },
  {
    "command": "git bisect good",
    "rule": "git-history"
  },
  {
    "command": "git bisect bad",
    "rule": "git-history"
  },
  {
    "command": "git bisect run ./test.sh",
    "rule": "git-history"
  },
  {
    "command": "git --no-pager push origin main",
    "rule": "git-history"
  },
  {
    "command": "git -P push origin main",
    "rule": "git-history"
  },
  {
    "command": "git -p push origin main",
    "rule": "git-history"
  },
  {
    "command": "git --git-dir=/tmp/repo/.git push",
    "rule": "git-history"
  },
  {
    "command": "git --work-tree=/tmp/repo push",
    "rule": "git-history"
  },
  {
    "command": "git -C repo checkout main",
    "rule": "git-history"
  },
  {
    "command": "git -C /tmp/repo -c user.name=test push",
    "rule": "git-history"
  },
  {
    "command": "git --bare push origin main",
    "rule": "git-history"
  },
  {
    "command": "git --literal-pathspecs push",
    "rule": "git-history"
  },
  {
    "command": "gh pr create --title \"PR\"",
    "rule": "github-write"
  },
  {
    "command": "gh pr merge 1",
    "rule": "github-write"
  },
  {
    "command": "gh pr close 1",
    "rule": "github-write"
  },
  {
    "command": "gh pr edit 1",
    "rule": "github-write"
  },
  {
    "command": "gh pr comment 1 -b \"lgtm\"",
    "rule": "github-write"
  },
  {
    "command": "gh pr review 1 --approve",
    "rule": "github-write"
  },
  {
    "command": "gh pr ready 1",
    "rule": "github-write"
  },
  {
    "command": "gh pr reopen 1",
    "rule": "github-write"
  },
  {
    "command": "gh release create v1.0.0",
    "rule": "github-write"
  },
  {
    "command": "gh release delete v1.0.0",
    "rule": "github-write"
  },
  {
    "command": "gh release edit v1.0.0",
    "rule": "github-write"
  },
  {
    "command": "gh release upload v1.0.0 asset.tar.gz",
    "rule": "github-write"
  },
  {
    "command": "gh repo create my-repo",
    "rule": "github-write"
  },
  {
    "command": "gh repo delete my-repo",
    "rule": "github-write"
  },
  {
    "command": "gh repo edit",
    "rule": "github-write"
  },
  {
    "command": "gh repo rename my-new-repo",
    "rule": "github-write"
  },
  {
    "command": "gh repo archive",
    "rule": "github-write"
  },
  {
    "command": "gh repo fork",
    "rule": "github-write"
  },
  {
    "command": "gh workflow run deploy.yml",
    "rule": "github-write"
  },
  {
    "command": "gh workflow enable deploy.yml",
    "rule": "github-write"
  },
  {
    "command": "gh workflow disable deploy.yml",
    "rule": "github-write"
  },
  {
    "command": "gh run rerun 123",
    "rule": "github-write"
  },
  {
    "command": "gh run cancel 123",
    "rule": "github-write"
  },
  {
    "command": "gh run delete 123",
    "rule": "github-write"
  },
  {
    "command": "gh issue create -t \"bug\"",
    "rule": "github-write"
  },
  {
    "command": "gh issue close 1",
    "rule": "github-write"
  },
  {
    "command": "gh issue edit 1",
    "rule": "github-write"
  },
  {
    "command": "gh issue comment 1 -b \"hi\"",
    "rule": "github-write"
  },
  {
    "command": "gh issue delete 1",
    "rule": "github-write"
  },
  {
    "command": "gh issue reopen 1",
    "rule": "github-write"
  },
  {
    "command": "gh api -X POST repos/owner/repo/issues",
    "rule": "github-write"
  },
  {
    "command": "gh api --method PUT repos/owner/repo/contents",
    "rule": "github-write"
  },
  {
    "command": "gh api -X PATCH repos/owner/repo",
    "rule": "github-write"
  },
  {
    "command": "gh api -X DELETE repos/owner/repo",
    "rule": "github-write"
  },
  {
    "command": "gh api repos/owner/repo/issues -f title=bug",
    "rule": "github-write"
  },
  {
    "command": "gh api repos/owner/repo/issues -F body=text",
    "rule": "github-write"
  },
  {
    "command": "gh api repos/owner/repo/issues --field title=bug",
    "rule": "github-write"
  },
  {
    "command": "gh api repos/owner/repo/issues --raw-field title=bug",
    "rule": "github-write"
  },
  {
    "command": "gh api repos/owner/repo/issues --input data.json",
    "rule": "github-write"
  },
  {
    "command": "kubectl -n prod apply -f k8s.yaml",
    "rule": "publish-deploy"
  },
  {
    "command": "kubectl --namespace=prod apply -f k8s.yaml",
    "rule": "publish-deploy"
  },
  {
    "command": "kubectl -n prod delete -f k8s.yaml",
    "rule": "publish-deploy"
  },
  {
    "command": "kubectl rollout restart deployment/app",
    "rule": "publish-deploy"
  },
  {
    "command": "kubectl scale deployment/app --replicas=3",
    "rule": "publish-deploy"
  },
  {
    "command": "kubectl patch deployment/app -p \"{}\"",
    "rule": "publish-deploy"
  },
  {
    "command": "kubectl replace -f k8s.yaml",
    "rule": "publish-deploy"
  },
  {
    "command": "helm -n prod upgrade rel chart",
    "rule": "publish-deploy"
  },
  {
    "command": "helm --namespace prod install rel chart",
    "rule": "publish-deploy"
  },
  {
    "command": "helm -n prod uninstall rel",
    "rule": "publish-deploy"
  },
  {
    "command": "helm rollback rel 1",
    "rule": "publish-deploy"
  },
  {
    "command": "terraform -chdir=infra apply",
    "rule": "publish-deploy"
  },
  {
    "command": "terraform -chdir=infra destroy",
    "rule": "publish-deploy"
  },
  {
    "command": "terraform apply",
    "rule": "publish-deploy"
  },
  {
    "command": "terraform destroy",
    "rule": "publish-deploy"
  },
  {
    "command": "docker push img:tag",
    "rule": "publish-deploy"
  },
  {
    "command": "./deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": "scripts/deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": "bash ./deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": "sh deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": "zsh scripts/deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": "source deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": ". deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": "npm publish",
    "rule": "publish-deploy"
  },
  {
    "command": "pnpm publish",
    "rule": "publish-deploy"
  },
  {
    "command": "yarn publish",
    "rule": "publish-deploy"
  },
  {
    "command": "bun publish",
    "rule": "publish-deploy"
  },
  {
    "command": "twine upload dist/*",
    "rule": "publish-deploy"
  },
  {
    "command": "cargo publish",
    "rule": "publish-deploy"
  },
  {
    "command": "gem push mygem.gem",
    "rule": "publish-deploy"
  },
  {
    "command": "vercel --prod",
    "rule": "publish-deploy"
  },
  {
    "command": "vercel deploy",
    "rule": "publish-deploy"
  },
  {
    "command": "firebase deploy",
    "rule": "publish-deploy"
  },
  {
    "command": "fly deploy",
    "rule": "publish-deploy"
  },
  {
    "command": "flyctl deploy",
    "rule": "publish-deploy"
  },
  {
    "command": "rsync -avz dist user@host:/var/www",
    "rule": "publish-deploy"
  },
  {
    "command": "scp file.txt user@host:path",
    "rule": "publish-deploy"
  },
  {
    "command": "php artisan migrate",
    "rule": "migration"
  },
  {
    "command": "php artisan migrate:rollback",
    "rule": "migration"
  },
  {
    "command": "php artisan db:wipe",
    "rule": "migration"
  },
  {
    "command": "rails db:migrate",
    "rule": "migration"
  },
  {
    "command": "rails db:drop",
    "rule": "migration"
  },
  {
    "command": "rails db:reset",
    "rule": "migration"
  },
  {
    "command": "rails db:rollback",
    "rule": "migration"
  },
  {
    "command": "rails db:setup",
    "rule": "migration"
  },
  {
    "command": "rails db:schema:load",
    "rule": "migration"
  },
  {
    "command": "rake db:migrate",
    "rule": "migration"
  },
  {
    "command": "prisma migrate dev",
    "rule": "migration"
  },
  {
    "command": "prisma migrate deploy",
    "rule": "migration"
  },
  {
    "command": "prisma migrate reset",
    "rule": "migration"
  },
  {
    "command": "prisma db push",
    "rule": "migration"
  },
  {
    "command": "alembic upgrade head",
    "rule": "migration"
  },
  {
    "command": "alembic downgrade -1",
    "rule": "migration"
  },
  {
    "command": "python manage.py migrate",
    "rule": "migration"
  },
  {
    "command": "./manage.py flush",
    "rule": "migration"
  },
  {
    "command": "knex migrate:latest",
    "rule": "migration"
  },
  {
    "command": "sequelize db:migrate",
    "rule": "migration"
  },
  {
    "command": "diesel migration run",
    "rule": "migration"
  },
  {
    "command": "goose up",
    "rule": "migration"
  },
  {
    "command": "flyway migrate",
    "rule": "migration"
  },
  {
    "command": "dropdb testdb",
    "rule": "migration"
  },
  {
    "command": "mysqladmin -u root drop testdb",
    "rule": "migration"
  },
  {
    "command": "redis-cli flushall",
    "rule": "migration"
  },
  {
    "command": "redis-cli flushdb",
    "rule": "migration"
  },
  {
    "command": "mysql -e \"DROP TABLE users\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP DATABASE test\"",
    "rule": "db-write"
  },
  {
    "command": "sqlite3 test.db \"DROP VIEW v\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP SCHEMA public\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP INDEX idx_name\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP MATERIALIZED VIEW mv_data\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP FUNCTION calculate_tax\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP PROCEDURE update_status\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP TRIGGER notify_user ON users\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP ROLE readonly\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP USER devuser\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP SEQUENCE user_id_seq\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP TYPE mood\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"DROP EXTENSION pgcrypto\"",
    "rule": "db-write"
  },
  {
    "command": "mariadb -e \"TRUNCATE users\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"ALTER TABLE users DROP COLUMN email\"",
    "rule": "db-write"
  },
  {
    "command": "mysql -e \"DELETE FROM users\"",
    "rule": "db-write"
  },
  {
    "command": "psql -c \"UPDATE users SET active = 0\"",
    "rule": "db-write"
  },
  {
    "command": "cockroach sql -e \"DROP TABLE users\"",
    "rule": "db-write"
  },
  {
    "command": "wp db query \"DROP TABLE wp_users\"",
    "rule": "db-write"
  },
  {
    "command": "psql -f migration.sql",
    "rule": "db-write"
  },
  {
    "command": "psql --file=migration.sql",
    "rule": "db-write"
  },
  {
    "command": "mysql -u root < dump.sql",
    "rule": "db-write"
  },
  {
    "command": "sqlite3 test.db < dump.sql",
    "rule": "db-write"
  },
  {
    "command": "psql -d test < dump.sql",
    "rule": "db-write"
  },
  {
    "command": "rm -rf /",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf /*",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -r ~",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf ~/",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf ~/*",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf $HOME",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf ${HOME}",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf ..",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf ../foo",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf .git",
    "rule": "rm-dangerous"
  },
  {
    "command": "rm -rf *",
    "rule": "rm-dangerous"
  },
  {
    "command": "git status",
    "rule": null
  },
  {
    "command": "git diff",
    "rule": null
  },
  {
    "command": "git log",
    "rule": null
  },
  {
    "command": "git show HEAD",
    "rule": null
  },
  {
    "command": "git blame file.txt",
    "rule": null
  },
  {
    "command": "git grep pattern",
    "rule": null
  },
  {
    "command": "git ls-files",
    "rule": null
  },
  {
    "command": "git rev-parse HEAD",
    "rule": null
  },
  {
    "command": "git fetch origin",
    "rule": null
  },
  {
    "command": "git branch",
    "rule": null
  },
  {
    "command": "git branch -a",
    "rule": null
  },
  {
    "command": "git branch -r",
    "rule": null
  },
  {
    "command": "git branch --list",
    "rule": null
  },
  {
    "command": "git branch --show-current",
    "rule": null
  },
  {
    "command": "git branch --contains HEAD",
    "rule": null
  },
  {
    "command": "git stash list",
    "rule": null
  },
  {
    "command": "git stash show",
    "rule": null
  },
  {
    "command": "git tag",
    "rule": null
  },
  {
    "command": "git tag -l",
    "rule": null
  },
  {
    "command": "git tag -l \"v1.*\"",
    "rule": null
  },
  {
    "command": "git tag --list",
    "rule": null
  },
  {
    "command": "git tag --sort=-v:refname",
    "rule": null
  },
  {
    "command": "git tag --points-at HEAD",
    "rule": null
  },
  {
    "command": "git tag --contains HEAD",
    "rule": null
  },
  {
    "command": "git tag -n",
    "rule": null
  },
  {
    "command": "git tag -n5",
    "rule": null
  },
  {
    "command": "git worktree list",
    "rule": null
  },
  {
    "command": "git remote -v",
    "rule": null
  },
  {
    "command": "git config --get user.name",
    "rule": null
  },
  {
    "command": "git bisect log",
    "rule": null
  },
  {
    "command": "git bisect visualize",
    "rule": null
  },
  {
    "command": "gh release list",
    "rule": null
  },
  {
    "command": "gh release view v1.0.0",
    "rule": null
  },
  {
    "command": "gh release download v1.0.0",
    "rule": null
  },
  {
    "command": "gh pr view 1",
    "rule": null
  },
  {
    "command": "gh pr list",
    "rule": null
  },
  {
    "command": "gh pr diff 1",
    "rule": null
  },
  {
    "command": "gh pr checks 1",
    "rule": null
  },
  {
    "command": "gh pr status",
    "rule": null
  },
  {
    "command": "gh issue view 1",
    "rule": null
  },
  {
    "command": "gh issue list",
    "rule": null
  },
  {
    "command": "gh run list",
    "rule": null
  },
  {
    "command": "gh run view 123",
    "rule": null
  },
  {
    "command": "gh repo view",
    "rule": null
  },
  {
    "command": "gh api repos/owner/repo",
    "rule": null
  },
  {
    "command": "gh api -X GET repos/owner/repo",
    "rule": null
  },
  {
    "command": "gh api --method GET repos/owner/repo -f query=1",
    "rule": null
  },
  {
    "command": "kubectl apply -f k8s.yaml --dry-run=client",
    "rule": null
  },
  {
    "command": "kubectl apply -f k8s.yaml --dry-run=server",
    "rule": null
  },
  {
    "command": "kubectl get pods",
    "rule": null
  },
  {
    "command": "kubectl describe pod app",
    "rule": null
  },
  {
    "command": "kubectl logs app",
    "rule": null
  },
  {
    "command": "helm template rel chart",
    "rule": null
  },
  {
    "command": "helm list",
    "rule": null
  },
  {
    "command": "helm status rel",
    "rule": null
  },
  {
    "command": "terraform plan",
    "rule": null
  },
  {
    "command": "terraform plan -out=tfplan",
    "rule": null
  },
  {
    "command": "docker build -t img .",
    "rule": null
  },
  {
    "command": "docker ps",
    "rule": null
  },
  {
    "command": "docker logs app",
    "rule": null
  },
  {
    "command": "bin/rails db:migrate:status",
    "rule": null
  },
  {
    "command": "rails db:migrate:status",
    "rule": null
  },
  {
    "command": "rails db:version",
    "rule": null
  },
  {
    "command": "php artisan migrate:status",
    "rule": null
  },
  {
    "command": "php artisan migrate --pretend",
    "rule": null
  },
  {
    "command": "alembic current",
    "rule": null
  },
  {
    "command": "alembic history",
    "rule": null
  },
  {
    "command": "prisma migrate status",
    "rule": null
  },
  {
    "command": "psql -c \"SELECT 1\"",
    "rule": null
  },
  {
    "command": "psql -c \"select 1\" && rg TRUNCATE",
    "rule": null
  },
  {
    "command": "mysql -e \"DELETE FROM t WHERE id = 1\"",
    "rule": null
  },
  {
    "command": "mysql -e \"UPDATE t SET a = 1 WHERE id = 2\"",
    "rule": null
  },
  {
    "command": "pg_dump mydb",
    "rule": null
  },
  {
    "command": "pg_dump mydb > backup.sql",
    "rule": null
  },
  {
    "command": "mysqldump mydb",
    "rule": null
  },
  {
    "command": "mysqldump mydb > backup.sql",
    "rule": null
  },
  {
    "command": "npm test",
    "rule": null
  },
  {
    "command": "npm run build",
    "rule": null
  },
  {
    "command": "cargo build",
    "rule": null
  },
  {
    "command": "cargo test",
    "rule": null
  },
  {
    "command": "eslint .",
    "rule": null
  },
  {
    "command": "rm -rf build/ dist/ node_modules/ ./tmp",
    "rule": null
  },
  {
    "command": "rsync -a src/ dst/",
    "rule": null
  },
  {
    "command": "grep -rn \"DROP TABLE\" .",
    "rule": null
  },
  {
    "command": "echo \"git push\"",
    "rule": null
  },
  {
    "command": "scp C:\\a.txt D:\\b.txt",
    "rule": null
  },
  {
    "command": "scp C:a.txt D:b.txt",
    "rule": null
  },
  {
    "command": "git --no-pager checkout -- a",
    "rule": "git-history"
  },
  {
    "command": "git -P stash",
    "rule": "git-history"
  },
  {
    "command": "gh api repos/x/comments -f body=x",
    "rule": "github-write"
  },
  {
    "command": "kubectl -n prod apply -f x",
    "rule": "publish-deploy"
  },
  {
    "command": "helm -n prod upgrade x y",
    "rule": "publish-deploy"
  },
  {
    "command": "bash scripts/deploy.sh",
    "rule": "publish-deploy"
  },
  {
    "command": "psql -f reset.sql",
    "rule": "db-write"
  },
  {
    "command": "mysql < dump.sql",
    "rule": "db-write"
  },
  {
    "command": "git branch --force main x",
    "rule": "git-history"
  },
  {
    "command": "gh release view v1",
    "rule": null
  },
  {
    "command": "kubectl apply --dry-run=client -f x",
    "rule": null
  },
  {
    "command": "pg_dump db",
    "rule": null
  },
  {
    "command": "gh pr view 3",
    "rule": null
  }
];
