<#
.SYNOPSIS
    Hexo 博客管理工具 - 支持 Obsidian 联动
.DESCRIPTION
    提供新建文章、同步 Obsidian 草稿、预览、部署等功能
.PARAMETER Command
    命令: new | publish | sync | server | deploy | list | open
.PARAMETER Title
    文章标题 (用于 new 命令)
.PARAMETER Tags
    文章标签，逗号分隔 (用于 new 命令)
.PARAMETER Category
    文章分类 (用于 new 命令)
.EXAMPLE
    .\blog-tool.ps1 new "我的新文章" -Tags "技术,Hexo" -Category "教程"
    .\blog-tool.ps1 publish "drafts/我的草稿"
    .\blog-tool.ps1 sync
    .\blog-tool.ps1 deploy
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet("new", "publish", "sync", "server", "deploy", "list", "open")]
    [string]$Command,

    [Parameter(Position = 1)]
    [string]$Title,

    [string]$Tags = "",
    [string]$Category = "",
    [string]$DraftsDir = "..\obsidian-drafts",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PostsDir = Join-Path $ScriptDir "source\_posts"
$DraftsPath = Join-Path $ScriptDir $DraftsDir

function Write-ColorLine {
    param([string]$Text, [string]$Color = "White")
    Write-Host $Text -ForegroundColor $Color
}

function Get-ValidFileName {
    param([string]$Name)
    $invalid = [IO.Path]::GetInvalidFileNameChars() -join ''
    $re = "[{0}]" -f [Regex]::Escape($invalid)
    return [Regex]::Replace($Name, $re, '-')
}

# ============================================================================
# 新建文章
# ============================================================================
function New-Post {
    param([string]$Title, [string]$Tags, [string]$Category)

    if (-not $Title) {
        Write-ColorLine "用法: .\blog-tool.ps1 new ""文章标题"" [-Tags ""标签1,标签2""] [-Category ""分类""]" "Yellow"
        return
    }

    $date = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $fileName = Get-ValidFileName $Title
    $filePath = Join-Path $PostsDir "$fileName.md"

    if ((Test-Path $filePath) -and -not $Force) {
        Write-ColorLine "文章已存在: $filePath" "Red"
        Write-ColorLine "加 -Force 覆盖已有文件" "Yellow"
        return
    }

    $tagList = if ($Tags) {
        ($Tags -split ',' | ForEach-Object { "  - $($_.Trim())" }) -join "`n"
    } else {
        "  - "
    }

    $categoryBlock = if ($Category) {
        "categories:`n  - $Category`n"
    } else {
        ""
    }

    $content = @"
---
title: $Title
date: $date
tags:
$tagList
$($categoryBlock)---
"@

    Set-Content -Path $filePath -Value $content -Encoding UTF8
    Write-ColorLine "✓ 文章已创建: $filePath" "Green"
    Write-ColorLine "  标题: $Title" "Cyan"
    if ($Tags) { Write-ColorLine "  标签: $Tags" "Cyan" }
    if ($Category) { Write-ColorLine "  分类: $Category" "Cyan" }

    return $filePath
}

# ============================================================================
# 从 Obsidian 草稿发布
# ============================================================================
function Publish-Draft {
    param([string]$DraftPath)

    if (-not $DraftPath) {
        Write-ColorLine "用法: .\blog-tool.ps1 publish ""drafts/草稿文件名""" "Yellow"
        Write-ColorLine "草稿文件夹: $DraftsPath" "Yellow"
        return
    }

    # 支持相对路径和绝对路径
    $fullDraftPath = if (Test-Path $DraftPath) {
        (Resolve-Path $DraftPath).Path
    } elseif (Test-Path (Join-Path $DraftsPath $DraftPath)) {
        (Resolve-Path (Join-Path $DraftsPath $DraftPath)).Path
    } else {
        # 尝试在 drafts 目录下匹配
        $match = Get-ChildItem -Path $DraftsPath -Recurse -Filter "*.md" |
            Where-Object { $_.BaseName -eq $DraftPath -or $_.Name -eq "$DraftPath.md" } |
            Select-Object -First 1
        if ($match) { $match.FullName } else { $null }
    }

    if (-not $fullDraftPath) {
        Write-ColorLine "找不到草稿: $DraftPath" "Red"
        return
    }

    $content = Get-Content -Path $fullDraftPath -Raw -Encoding UTF8
    $fileName = [IO.Path]::GetFileNameWithoutExtension($fullDraftPath)

    # 解析 Obsidian frontmatter
    $hasFrontmatter = $content -match '^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$'
    $meta = @{}
    $body = $content

    if ($hasFrontmatter) {
        $fmContent = $Matches[1]
        $body = $Matches[2]
        foreach ($line in ($fmContent -split '\n')) {
            if ($line -match '^(tags?|categories?|category|title|date|aliases?):\s*(.*)') {
                $key = $Matches[1]
                $val = $Matches[2].Trim()
                if ($meta.ContainsKey($key)) {
                    $meta[$key] += ", $val"
                } else {
                    $meta[$key] = $val
                }
            }
        }
    }

    # 构建 Hexo frontmatter
    $title = if ($meta['title']) { $meta['title'] } else { $fileName }
    $date = if ($meta['date']) { $meta['date'] } else { Get-Date -Format "yyyy-MM-dd HH:mm:ss" }
    $tags = if ($meta['tags']) {
        ($meta['tags'] -split ',' | ForEach-Object { "  - $($_.Trim())" }) -join "`n"
    } else {
        "  - "
    }
    $categoryBlock = if ($meta['category'] -or $meta['categories']) {
        $cat = if ($meta['categories']) { $meta['categories'] } else { $meta['category'] }
        "categories:`n  - $cat`n"
    } else { "" }

    $hexoContent = @"
---
title: $title
date: $date
tags:
$tags
$($categoryBlock)---
$body
"@

    $targetPath = Join-Path $PostsDir "$fileName.md"
    Set-Content -Path $targetPath -Value $hexoContent -Encoding UTF8

    # 在源文件头部添加 hexo 标记，表示已发布
    $publishedMarker = "`n<!-- hexo-published: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') -->"
    $existingContent = Get-Content -Path $fullDraftPath -Raw -Encoding UTF8
    if ($existingContent -notmatch 'hexo-published') {
        Add-Content -Path $fullDraftPath -Value $publishedMarker -Encoding UTF8
    }

    Write-ColorLine "✓ 草稿已发布: $targetPath" "Green"
}

# ============================================================================
# 同步 Obsidian vault 到 Hexo posts
# ============================================================================
function Sync-Obsidian {
    if (-not (Test-Path $DraftsPath)) {
        Write-ColorLine "Obsidian 草稿目录不存在: $DraftsPath" "Yellow"
        Write-ColorLine "请在 Obsidian 中创建一个 vault，目录设为: $DraftsPath" "Yellow"
        Write-ColorLine "或者在脚本调用时指定: .\blog-tool.ps1 sync -DraftsDir ""D:\你的ObsidianVault""" "Yellow"
        return
    }

    $count = 0
    $drafts = Get-ChildItem -Path $DraftsPath -Recurse -Filter "*.md" |
        Where-Object { $_.Name -notlike "*.excalidraw.md" }

    foreach ($draft in $drafts) {
        $content = Get-Content -Path $draft.FullName -Raw -Encoding UTF8

        # 检查是否有 hexo-published 标记
        if ($content -match 'hexo-published:\s*(\d{4}-\d{2}-\d{2})') {
            $publishedDate = [DateTime]$Matches[1]
            $draftModified = $draft.LastWriteTime

            # 如果草稿在发布后被修改过，提示用户
            if ($draftModified -gt $publishedDate) {
                Write-ColorLine "  草稿已更新（需重新发布）: $($draft.Name)" "Yellow"
            }
            continue
        }

        # 检查是否有 publish: true 标记
        if ($content -match 'publish:\s*true') {
            Write-ColorLine "  发现待发布草稿: $($draft.Name)" "Cyan"
            Publish-Draft -DraftPath $draft.FullName
            $count++
        }
    }

    if ($count -eq 0) {
        Write-ColorLine "没有发现新的待发布草稿" "Gray"
        Write-ColorLine "在 Obsidian 草稿的 frontmatter 中添加 publish: true 即可标记为待发布" "Gray"
    } else {
        Write-ColorLine "✓ 共发布 $count 篇文章" "Green"
    }
}

# ============================================================================
# 本地预览
# ============================================================================
function Start-Server {
    Write-ColorLine "启动本地预览服务器..." "Cyan"
    Set-Location $ScriptDir
    npx hexo server --open
}

# ============================================================================
# 部署
# ============================================================================
function Invoke-Deploy {
    Write-ColorLine "1/3 清理旧文件..." "Cyan"
    npx hexo clean
    if ($LASTEXITCODE -ne 0) { throw "clean 失败" }

    Write-ColorLine "2/3 生成静态文件..." "Cyan"
    npx hexo generate
    if ($LASTEXITCODE -ne 0) { throw "generate 失败" }

    Write-ColorLine "3/3 部署到 GitHub Pages..." "Cyan"
    npx hexo deploy
    if ($LASTEXITCODE -ne 0) { throw "deploy 失败" }

    Write-ColorLine "✓ 部署完成！稍后访问 https://lzwpluto.github.io" "Green"
}

# ============================================================================
# 文章列表
# ============================================================================
function List-Posts {
    Write-ColorLine "=== 博客文章列表 ===" "Cyan"
    $posts = Get-ChildItem -Path $PostsDir -Filter "*.md" | Sort-Object LastWriteTime -Descending

    if ($posts.Count -eq 0) {
        Write-ColorLine "暂无文章" "Gray"
        return
    }

    foreach ($post in $posts) {
        $content = Get-Content -Path $post.FullName -Raw -Encoding UTF8
        $title = if ($content -match 'title:\s*(.+)$') { $Matches[1].Trim() } else { $post.BaseName }
        $date = if ($content -match 'date:\s*(.+)$') { $Matches[1].Trim() } else { $post.LastWriteTime.ToString("yyyy-MM-dd HH:mm") }
        $tags = if ($content -match 'tags:\s*\n((?:\s*-\s*.+\n?)*)') {
            ($Matches[1] -split '\n' | Where-Object { $_ -match '-\s*(.+)' } | ForEach-Object { $Matches[1].Trim() }) -join ', '
        } else { "" }

        Write-ColorLine "  $($post.Name)" "White"
        Write-ColorLine "    标题: $title  日期: $date" "Gray"
        if ($tags -and $tags -ne '-') { Write-ColorLine "    标签: $tags" "Gray" }
    }
    Write-ColorLine "共 $($posts.Count) 篇文章" "Cyan"
}

# ============================================================================
# 在 Obsidian/编辑器 中打开文章
# ============================================================================
function Open-Post {
    param([string]$Name)

    if (-not $Name) {
        # 打开 posts 目录
        Start-Process $PostsDir
        return
    }

    $match = Get-ChildItem -Path $PostsDir -Filter "*.md" |
        Where-Object { $_.BaseName -eq $Name -or $_.Name -eq "$Name.md" } |
        Select-Object -First 1

    if ($match) {
        Start-Process $match.FullName
    } else {
        Write-ColorLine "找不到文章: $Name" "Red"
    }
}

# ============================================================================
# 主入口
# ============================================================================
switch ($Command) {
    "new"     { New-Post -Title $Title -Tags $Tags -Category $Category }
    "publish" { Publish-Draft -DraftPath $Title }
    "sync"    { Sync-Obsidian }
    "server"  { Start-Server }
    "deploy"  { Invoke-Deploy }
    "list"    { List-Posts }
    "open"    { Open-Post -Name $Title }
    default {
        Write-ColorLine "Hexo 博客管理工具" "Cyan"
        Write-ColorLine "==================" "Cyan"
        Write-ColorLine ""
        Write-ColorLine "用法: .\blog-tool.ps1 <命令> [参数]" "White"
        Write-ColorLine ""
        Write-ColorLine "命令:" "Yellow"
        Write-ColorLine "  new     ""标题""    创建新文章 [-Tags ""标签""] [-Category ""分类""]" "White"
        Write-ColorLine "  publish ""草稿""   发布 Obsidian 草稿" "White"
        Write-ColorLine "  sync              同步 Obsidian Vault 中标记 publish: true 的草稿" "White"
        Write-ColorLine "  server            启动本地预览 (自动打开浏览器)" "White"
        Write-ColorLine "  deploy            清理 → 生成 → 部署 一键发布" "White"
        Write-ColorLine "  list              列出所有文章" "White"
        Write-ColorLine "  open    [文章名]   打开文章目录/指定文章" "White"
        Write-ColorLine ""
        Write-ColorLine "与 Obsidian 联动:" "Yellow"
        Write-ColorLine "  1. 在 Obsidian 中创建 vault，目录指向: ..\obsidian-drafts (或自定义)" "White"
        Write-ColorLine "  2. 写博客时在 frontmatter 中设置: publish: true" "White"
        Write-ColorLine "  3. 运行 .\blog-tool.ps1 sync 自动发布草稿" "White"
        Write-ColorLine ""
        Write-ColorLine "Obsidian frontmatter 示例:" "Cyan"
        Write-ColorLine "  ---" "Gray"
        Write-ColorLine "  title: 我的文章标题" "Gray"
        Write-ColorLine "  tags: 技术,教程" "Gray"
        Write-ColorLine "  category: 开发" "Gray"
        Write-ColorLine "  publish: true" "Gray"
        Write-ColorLine "  ---" "Gray"
    }
}
