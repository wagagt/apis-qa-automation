param (
    [string]$reportName = "newman-report.json",
    [string]$folderName
)

# Ruta a los archivos de colección y entorno
$collectionFile = ".\BPXAPI.postman_collection.json"
$environmentFile = ".\BPXAPI.postman_environment.json"

# Comando base
$newmanArgs = @(
    "run", $collectionFile,
    "-e", $environmentFile,
    "--reporters", "cli,json,htmlextra",
    "--reporter-json-export", $reportName,
    "--reporter-htmlextra-export", "$($reportName -replace '\.json$', '.html')"
)

# Agrega folder si se especificó
if ($folderName) {
    $newmanArgs += "--folder"
    $newmanArgs += $folderName
}

# Ejecutar Newman
newman @newmanArgs
