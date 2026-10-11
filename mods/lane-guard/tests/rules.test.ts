import { test, expect } from 'claude-code/testing';
import { forbiddenReason, mcpForbidden } from '../hooks/rules.js';

type TestCase = {
  command: string;
  expectedRule: string | null;
  expectedLabel?: string;
  name: string;
};

const testCases: TestCase[] = [
  // [git-history]
  { command: 'git commit -m "fix"', expectedRule: 'git-history', name: 'git commit' },
  { command: 'git push origin main', expectedRule: 'git-history', name: 'git push' },
  { command: 'git merge feature', expectedRule: 'git-history', name: 'git merge' },
  { command: 'git rebase main', expectedRule: 'git-history', name: 'git rebase' },
  { command: 'git reset --hard HEAD~1', expectedRule: 'git-history', name: 'git reset' },
  { command: 'git restore .', expectedRule: 'git-history', name: 'git restore' },
  { command: 'git checkout main', expectedRule: 'git-history', name: 'git checkout' },
  { command: 'git switch main', expectedRule: 'git-history', name: 'git switch' },
  { command: 'git clean -fd', expectedRule: 'git-history', name: 'git clean' },
  { command: 'git cherry-pick abc1234', expectedRule: 'git-history', name: 'git cherry-pick' },
  { command: 'git revert HEAD', expectedRule: 'git-history', name: 'git revert' },
  { command: 'git am patch.patch', expectedRule: 'git-history', name: 'git am' },
  { command: 'git tag v1.0.0', expectedRule: 'git-history', name: 'git tag creation' },
  { command: 'git tag -d v1.0.0', expectedRule: 'git-history', name: 'git tag delete' },
  { command: 'git stash', expectedRule: 'git-history', name: 'git stash bare' },
  { command: 'git stash pop', expectedRule: 'git-history', name: 'git stash pop' },
  { command: 'git branch -d feat', expectedRule: 'git-history', name: 'git branch -d' },
  { command: 'git branch -D feat', expectedRule: 'git-history', name: 'git branch -D' },
  { command: 'git branch -m old new', expectedRule: 'git-history', name: 'git branch -m' },
  { command: 'git branch -M old new', expectedRule: 'git-history', name: 'git branch -M' },
  { command: 'git branch --delete feat', expectedRule: 'git-history', name: 'git branch --delete' },
  { command: 'git branch --move old new', expectedRule: 'git-history', name: 'git branch --move' },
  { command: 'git branch -f feat', expectedRule: 'git-history', name: 'git branch -f' },
  { command: 'git worktree remove tmp', expectedRule: 'git-history', name: 'git worktree remove' },
  { command: 'git worktree prune', expectedRule: 'git-history', name: 'git worktree prune' },
  { command: 'git update-ref refs/heads/main HEAD', expectedRule: 'git-history', name: 'git update-ref' },
  { command: 'git filter-branch --tree-filter "rm -f p" HEAD', expectedRule: 'git-history', name: 'git filter-branch' },

  // [github-write]
  { command: 'gh pr create --title "PR"', expectedRule: 'github-write', name: 'gh pr create' },
  { command: 'gh pr merge 1', expectedRule: 'github-write', name: 'gh pr merge' },
  { command: 'gh pr close 1', expectedRule: 'github-write', name: 'gh pr close' },
  { command: 'gh pr edit 1', expectedRule: 'github-write', name: 'gh pr edit' },
  { command: 'gh pr comment 1 -b "lgtm"', expectedRule: 'github-write', name: 'gh pr comment' },
  { command: 'gh pr review 1 --approve', expectedRule: 'github-write', name: 'gh pr review' },
  { command: 'gh pr ready 1', expectedRule: 'github-write', name: 'gh pr ready' },
  { command: 'gh pr reopen 1', expectedRule: 'github-write', name: 'gh pr reopen' },
  { command: 'gh release create v1.0.0', expectedRule: 'github-write', name: 'gh release' },
  { command: 'gh repo create my-repo', expectedRule: 'github-write', name: 'gh repo create' },
  { command: 'gh repo delete my-repo', expectedRule: 'github-write', name: 'gh repo delete' },
  { command: 'gh repo edit', expectedRule: 'github-write', name: 'gh repo edit' },
  { command: 'gh repo rename my-new-repo', expectedRule: 'github-write', name: 'gh repo rename' },
  { command: 'gh repo archive', expectedRule: 'github-write', name: 'gh repo archive' },
  { command: 'gh repo fork', expectedRule: 'github-write', name: 'gh repo fork' },
  { command: 'gh workflow run deploy.yml', expectedRule: 'github-write', name: 'gh workflow run' },
  { command: 'gh workflow enable deploy.yml', expectedRule: 'github-write', name: 'gh workflow enable' },
  { command: 'gh workflow disable deploy.yml', expectedRule: 'github-write', name: 'gh workflow disable' },
  { command: 'gh issue create -t "bug"', expectedRule: 'github-write', name: 'gh issue create' },
  { command: 'gh issue close 1', expectedRule: 'github-write', name: 'gh issue close' },
  { command: 'gh issue edit 1', expectedRule: 'github-write', name: 'gh issue edit' },
  { command: 'gh issue comment 1 -b "hi"', expectedRule: 'github-write', name: 'gh issue comment' },
  { command: 'gh issue delete 1', expectedRule: 'github-write', name: 'gh issue delete' },
  { command: 'gh issue reopen 1', expectedRule: 'github-write', name: 'gh issue reopen' },
  { command: 'gh api -X POST repos/owner/repo/issues', expectedRule: 'github-write', name: 'gh api -X POST' },
  { command: 'gh api --method PUT repos/owner/repo/contents', expectedRule: 'github-write', name: 'gh api --method PUT' },
  { command: 'gh api -X PATCH repos/owner/repo', expectedRule: 'github-write', name: 'gh api -X PATCH' },
  { command: 'gh api -X DELETE repos/owner/repo', expectedRule: 'github-write', name: 'gh api -X DELETE' },

  // [publish-deploy]
  { command: 'npm publish', expectedRule: 'publish-deploy', name: 'npm publish' },
  { command: 'pnpm publish', expectedRule: 'publish-deploy', name: 'pnpm publish' },
  { command: 'yarn publish', expectedRule: 'publish-deploy', name: 'yarn publish' },
  { command: 'bun publish', expectedRule: 'publish-deploy', name: 'bun publish' },
  { command: 'twine upload dist/*', expectedRule: 'publish-deploy', name: 'twine upload' },
  { command: 'cargo publish', expectedRule: 'publish-deploy', name: 'cargo publish' },
  { command: 'gem push mygem.gem', expectedRule: 'publish-deploy', name: 'gem push' },
  { command: 'docker push img:tag', expectedRule: 'publish-deploy', name: 'docker push' },
  { command: 'kubectl apply -f k8s.yaml', expectedRule: 'publish-deploy', name: 'kubectl apply' },
  { command: 'kubectl delete -f k8s.yaml', expectedRule: 'publish-deploy', name: 'kubectl delete' },
  { command: 'kubectl rollout restart deployment/app', expectedRule: 'publish-deploy', name: 'kubectl rollout' },
  { command: 'kubectl scale deployment/app --replicas=3', expectedRule: 'publish-deploy', name: 'kubectl scale' },
  { command: 'kubectl patch deployment/app -p "{}"', expectedRule: 'publish-deploy', name: 'kubectl patch' },
  { command: 'kubectl replace -f k8s.yaml', expectedRule: 'publish-deploy', name: 'kubectl replace' },
  { command: 'helm install rel chart', expectedRule: 'publish-deploy', name: 'helm install' },
  { command: 'helm upgrade rel chart', expectedRule: 'publish-deploy', name: 'helm upgrade' },
  { command: 'helm uninstall rel', expectedRule: 'publish-deploy', name: 'helm uninstall' },
  { command: 'helm rollback rel 1', expectedRule: 'publish-deploy', name: 'helm rollback' },
  { command: 'terraform apply', expectedRule: 'publish-deploy', name: 'terraform apply' },
  { command: 'terraform destroy', expectedRule: 'publish-deploy', name: 'terraform destroy' },
  { command: 'vercel --prod', expectedRule: 'publish-deploy', name: 'vercel --prod' },
  { command: 'vercel deploy', expectedRule: 'publish-deploy', name: 'vercel deploy' },
  { command: 'firebase deploy', expectedRule: 'publish-deploy', name: 'firebase deploy' },
  { command: 'fly deploy', expectedRule: 'publish-deploy', name: 'fly deploy' },
  { command: 'flyctl deploy', expectedRule: 'publish-deploy', name: 'flyctl deploy' },
  { command: './deploy.sh', expectedRule: 'publish-deploy', name: './deploy.sh' },
  { command: 'scripts/deploy.sh', expectedRule: 'publish-deploy', name: 'scripts/deploy.sh' },
  { command: 'rsync -avz dist user@host:/var/www', expectedRule: 'publish-deploy', name: 'rsync remote' },
  { command: 'scp file.txt user@host:path', expectedRule: 'publish-deploy', name: 'scp remote' },

  // [migration]
  { command: 'php artisan migrate', expectedRule: 'migration', name: 'php artisan migrate' },
  { command: 'php artisan migrate:rollback', expectedRule: 'migration', name: 'php artisan migrate:rollback' },
  { command: 'php artisan db:wipe', expectedRule: 'migration', name: 'php artisan db:wipe' },
  { command: 'rails db:migrate', expectedRule: 'migration', name: 'rails db:migrate' },
  { command: 'rails db:drop', expectedRule: 'migration', name: 'rails db:drop' },
  { command: 'rails db:reset', expectedRule: 'migration', name: 'rails db:reset' },
  { command: 'rails db:rollback', expectedRule: 'migration', name: 'rails db:rollback' },
  { command: 'rails db:setup', expectedRule: 'migration', name: 'rails db:setup' },
  { command: 'rails db:schema:load', expectedRule: 'migration', name: 'rails db:schema:load' },
  { command: 'rake db:migrate', expectedRule: 'migration', name: 'rake db:migrate' },
  { command: 'prisma migrate dev', expectedRule: 'migration', name: 'prisma migrate dev' },
  { command: 'prisma migrate deploy', expectedRule: 'migration', name: 'prisma migrate deploy' },
  { command: 'prisma migrate reset', expectedRule: 'migration', name: 'prisma migrate reset' },
  { command: 'prisma db push', expectedRule: 'migration', name: 'prisma db push' },
  { command: 'alembic upgrade head', expectedRule: 'migration', name: 'alembic upgrade' },
  { command: 'alembic downgrade -1', expectedRule: 'migration', name: 'alembic downgrade' },
  { command: 'python manage.py migrate', expectedRule: 'migration', name: 'python manage.py migrate' },
  { command: './manage.py flush', expectedRule: 'migration', name: './manage.py flush' },
  { command: 'knex migrate:latest', expectedRule: 'migration', name: 'knex migrate:latest' },
  { command: 'sequelize db:migrate', expectedRule: 'migration', name: 'sequelize db:migrate' },
  { command: 'diesel migration run', expectedRule: 'migration', name: 'diesel migration run' },
  { command: 'goose up', expectedRule: 'migration', name: 'goose up' },
  { command: 'flyway migrate', expectedRule: 'migration', name: 'flyway migrate' },
  { command: 'dropdb testdb', expectedRule: 'migration', name: 'dropdb' },
  { command: 'mysqladmin -u root drop testdb', expectedRule: 'migration', name: 'mysqladmin drop' },
  { command: 'redis-cli flushall', expectedRule: 'migration', name: 'redis-cli flushall' },
  { command: 'redis-cli flushdb', expectedRule: 'migration', name: 'redis-cli flushdb' },

  // [db-write]
  { command: 'mysql -e "DROP TABLE users"', expectedRule: 'db-write', name: 'mysql DROP TABLE' },
  { command: 'psql -c "DROP DATABASE test"', expectedRule: 'db-write', name: 'psql DROP DATABASE' },
  { command: 'sqlite3 test.db "DROP VIEW v"', expectedRule: 'db-write', name: 'sqlite3 DROP VIEW' },
  { command: 'mariadb -e "TRUNCATE users"', expectedRule: 'db-write', name: 'mariadb TRUNCATE' },
  { command: 'psql -c "ALTER TABLE users DROP COLUMN email"', expectedRule: 'db-write', name: 'psql ALTER TABLE DROP' },
  { command: 'mysql -e "DELETE FROM users"', expectedRule: 'db-write', name: 'mysql DELETE without WHERE' },
  { command: 'psql -c "UPDATE users SET active = 0"', expectedRule: 'db-write', name: 'psql UPDATE without WHERE' },
  { command: 'cockroach sql -e "DROP TABLE users"', expectedRule: 'db-write', name: 'cockroach sql DROP TABLE' },
  { command: 'wp db query "DROP TABLE wp_users"', expectedRule: 'db-write', name: 'wp db query DROP TABLE' },

  // [rm-dangerous]
  { command: 'rm -rf /', expectedRule: 'rm-dangerous', name: 'rm -rf /' },
  { command: 'rm -rf /*', expectedRule: 'rm-dangerous', name: 'rm -rf /*' },
  { command: 'rm -r ~', expectedRule: 'rm-dangerous', name: 'rm -r ~' },
  { command: 'rm -rf ~/', expectedRule: 'rm-dangerous', name: 'rm -rf ~/' },
  { command: 'rm -rf ~/*', expectedRule: 'rm-dangerous', name: 'rm -rf ~/*' },
  { command: 'rm -rf $HOME', expectedRule: 'rm-dangerous', name: 'rm -rf $HOME' },
  { command: 'rm -rf ${HOME}', expectedRule: 'rm-dangerous', name: 'rm -rf ${HOME}' },
  { command: 'rm -rf ..', expectedRule: 'rm-dangerous', name: 'rm -rf ..' },
  { command: 'rm -rf ../foo', expectedRule: 'rm-dangerous', name: 'rm -rf ../foo' },
  { command: 'rm -rf .git', expectedRule: 'rm-dangerous', name: 'rm -rf .git' },
  { command: 'rm -rf *', expectedRule: 'rm-dangerous', name: 'rm -rf *' },

  // Must NOT match (expectedRule: null)
  { command: 'git status', expectedRule: null, name: 'git status allowed' },
  { command: 'git diff', expectedRule: null, name: 'git diff allowed' },
  { command: 'git log', expectedRule: null, name: 'git log allowed' },
  { command: 'git show HEAD', expectedRule: null, name: 'git show allowed' },
  { command: 'git blame file.txt', expectedRule: null, name: 'git blame allowed' },
  { command: 'git grep pattern', expectedRule: null, name: 'git grep allowed' },
  { command: 'git ls-files', expectedRule: null, name: 'git ls-files allowed' },
  { command: 'git rev-parse HEAD', expectedRule: null, name: 'git rev-parse allowed' },
  { command: 'git fetch origin', expectedRule: null, name: 'git fetch allowed' },
  { command: 'git branch', expectedRule: null, name: 'bare git branch allowed' },
  { command: 'git stash list', expectedRule: null, name: 'git stash list allowed' },
  { command: 'git stash show', expectedRule: null, name: 'git stash show allowed' },
  { command: 'git tag', expectedRule: null, name: 'bare git tag allowed' },
  { command: 'git tag -l "v1.*"', expectedRule: null, name: 'git tag -l allowed' },
  { command: 'git tag --list', expectedRule: null, name: 'git tag --list allowed' },
  { command: 'npm test', expectedRule: null, name: 'test runner allowed' },
  { command: 'cargo build', expectedRule: null, name: 'build tool allowed' },
  { command: 'eslint .', expectedRule: null, name: 'linter allowed' },
  { command: 'psql -c "SELECT 1"', expectedRule: null, name: 'psql SELECT 1 allowed' },
  { command: 'mysql -e "DELETE FROM t WHERE id = 1"', expectedRule: null, name: 'mysql DELETE with WHERE allowed' },
  { command: 'php artisan migrate:status', expectedRule: null, name: 'artisan migrate:status allowed' },
  { command: 'php artisan migrate --pretend', expectedRule: null, name: 'artisan migrate --pretend allowed' },
  { command: 'rm -rf build/ dist/ node_modules/ ./tmp', expectedRule: null, name: 'rm build/dist allowed' },
  { command: 'rsync -a src/ dst/', expectedRule: null, name: 'rsync local allowed' },
  { command: 'grep -rn "DROP TABLE" .', expectedRule: null, name: 'grep DROP TABLE allowed' },
  { command: 'echo "git push"', expectedRule: null, name: 'echo git push allowed' },

  // Segment / wrapper edge cases
  { command: 'cd x && git push', expectedRule: 'git-history', name: 'cd x && git push' },
  { command: 'sudo -E git reset --hard', expectedRule: 'git-history', name: 'sudo -E git reset --hard' },
  { command: 'FOO=1 git commit', expectedRule: 'git-history', name: 'FOO=1 git commit' },
  { command: 'bash -c "git push"', expectedRule: 'git-history', name: 'bash -c git push' },
  { command: '$(git stash)', expectedRule: 'git-history', name: '$(git stash)' },
  { command: 'git -C repo checkout main', expectedRule: 'git-history', name: 'git -C repo checkout main' },
];

for (const tc of testCases) {
  test(`forbiddenReason: ${tc.name}`, () => {
    const res = forbiddenReason(tc.command);
    if (tc.expectedRule === null) {
      expect(res).toBeNull();
    } else {
      expect(res).not.toBeNull();
      expect(res?.rule).toBe(tc.expectedRule);
    }
  });
}

test('mcpForbidden: destructive SQL detected', () => {
  const res = mcpForbidden({ sql: 'DROP TABLE users' });
  expect(res).not.toBeNull();
  expect(res?.rule).toBe('db-write');
});

test('mcpForbidden: migration detected', () => {
  const res = mcpForbidden({ cmd: 'php artisan migrate' });
  expect(res).not.toBeNull();
  expect(res?.rule).toBe('migration');
});

test('mcpForbidden: non-destructive query allowed', () => {
  const res = mcpForbidden({ sql: 'SELECT * FROM users WHERE id = 1' });
  expect(res).toBeNull();
});

test('mcpForbidden: git command allowed in MCP (not checked)', () => {
  const res = mcpForbidden({ command: 'git push' });
  expect(res).toBeNull();
});

test('parity with the Python rules: drives and manage.py', () => {
  expect(forbiddenReason('scp C:\\a.txt D:\\b.txt')).toBeNull();
  expect(forbiddenReason('scp C:a.txt D:b.txt')).toBeNull();
  expect(forbiddenReason('python3 manage.py migrate')?.rule).toBe('migration');
  expect(forbiddenReason('rsync -a dist/ root@host:/var/www')?.rule).toBe('publish-deploy');
});
