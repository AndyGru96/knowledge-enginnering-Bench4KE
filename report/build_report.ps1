$ErrorActionPreference = 'Stop'
$reportDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildDir = Join-Path $reportDir 'build'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$latexmk = Get-Command latexmk -ErrorAction SilentlyContinue
$pdflatex = Get-Command pdflatex -ErrorAction SilentlyContinue
$bibtex = Get-Command bibtex -ErrorAction SilentlyContinue
$tectonic = Get-Command tectonic -ErrorAction SilentlyContinue

Push-Location $reportDir
try {
    if ($tectonic) {
        & $tectonic.Source -o $buildDir --keep-logs main.tex
    }
    elseif ($latexmk) {
        & $latexmk.Source -pdf -interaction=nonstopmode -halt-on-error -outdir=$buildDir main.tex
    }
    elseif ($pdflatex -and $bibtex) {
        & $pdflatex.Source -interaction=nonstopmode -halt-on-error -output-directory=$buildDir main.tex
        Push-Location $buildDir
        try { & $bibtex.Source main } finally { Pop-Location }
        & $pdflatex.Source -interaction=nonstopmode -halt-on-error -output-directory=$buildDir main.tex
        & $pdflatex.Source -interaction=nonstopmode -halt-on-error -output-directory=$buildDir main.tex
    }
    else {
        throw 'No LaTeX toolchain found. Install Tectonic, latexmk, or pdflatex plus bibtex.'
    }
    Copy-Item -LiteralPath (Join-Path $buildDir 'main.pdf') -Destination (Join-Path $reportDir 'FINAL_REPORT.pdf') -Force
}
finally {
    Pop-Location
}

Write-Output "Built $reportDir\FINAL_REPORT.pdf"
