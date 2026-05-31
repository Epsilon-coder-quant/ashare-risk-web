# 推送到 GitHub

在 GitHub 新建空仓库后，把仓库地址替换到下面命令中：

```bash
cd "/Users/sisyphus/Documents/Codex/2026-05-27/outlook/ashare-risk-render"
git remote add origin https://github.com/YOUR_USER/ashare-risk-web.git
git push -u origin main
```

如果你选择 SSH：

```bash
git remote add origin git@github.com:YOUR_USER/ashare-risk-web.git
git push -u origin main
```

推送后，在 Render 选择 `New` -> `Blueprint`，连接这个仓库即可。
