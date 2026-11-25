param([string]$ImageDirectory = $env:OCTAVE_LAB_IMAGE_DIRECTORY)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]

function Wait-WinRT($Operation, [Type]$ResultType) {
    $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

$languages = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
$language = $null
if ($env:OCTAVE_LAB_OCR_LANGUAGE) {
    $requested = $env:OCTAVE_LAB_OCR_LANGUAGE
    $language = $languages | Where-Object { $_.LanguageTag -eq $requested -or $_.LanguageTag.StartsWith($requested + '-') } | Select-Object -First 1
    if (!$language) { throw "Requested Windows OCR language is unavailable: $requested" }
}
if (!$language) { $language = $languages | Where-Object LanguageTag -Like 'zh-Hans*' | Select-Object -First 1 }
if (!$language) { $language = $languages | Where-Object LanguageTag -Like 'zh*' | Select-Object -First 1 }
if (!$language) { $language = $languages | Where-Object LanguageTag -Like 'en*' | Select-Object -First 1 }
if (!$language) { $language = $languages | Select-Object -First 1 }
if (!$language) { throw 'No Windows OCR language installed.' }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
$pages = @()
foreach ($image in (Get-ChildItem -LiteralPath $ImageDirectory -Filter '*.png' | Sort-Object Name)) {
    $stream = $null
    $bitmap = $null
    try {
        $file = Wait-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($image.FullName)) ([Windows.Storage.StorageFile])
        $stream = Wait-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Wait-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $pages += @{ name = $image.Name; text = (($result.Lines | ForEach-Object { $_.Text }) -join "`n"); error = '' }
    } catch {
        $pages += @{ name = $image.Name; text = ''; error = $_.Exception.Message }
    } finally {
        if ($bitmap) { $bitmap.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}
ConvertTo-Json -InputObject @{ language = $language.LanguageTag; pages = $pages } -Depth 4 -Compress
