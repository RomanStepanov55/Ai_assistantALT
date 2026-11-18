# Persistent WinRT OCR server for Muscle Legends bot.
# Protocol: one image path per line on stdin -> one JSON line on stdout.
# The OCR engine is created ONCE (vs 1-6s process spawn per call).
param([int]$Scale = 3)
$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }
    function Await($WinRtTask, $ResultType) {
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $netTask.Wait(-1) | Out-Null
        return $netTask.Result
    }

    [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
    [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language("en-US"))) } catch {}
    }
    if ($null -eq $engine) {
        try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language("ru-RU"))) } catch {}
    }
    if ($null -eq $engine) {
        Write-Output (@{ lines = @(); error = "no engine" } | ConvertTo-Json -Compress -Depth 3)
        [Console]::Out.Flush()
        exit 1
    }

    while ($true) {
        $line = [Console]::In.ReadLine()
        if ($null -eq $line) { break }
        $path = $line.Trim()
        if ($path -eq '') { continue }
        try {
            $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
            $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

            $linesOut = New-Object System.Collections.ArrayList
            foreach ($ln in $result.Lines) {
                $minX = [double]::MaxValue; $minY = [double]::MaxValue
                $maxX = [double]::MinValue; $maxY = [double]::MinValue
                foreach ($word in $ln.Words) {
                    $r = $word.BoundingRect
                    if ($r.X -lt $minX) { $minX = $r.X }
                    if ($r.Y -lt $minY) { $minY = $r.Y }
                    if (($r.X + $r.Width) -gt $maxX) { $maxX = $r.X + $r.Width }
                    if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
                }
                if ($minX -eq [double]::MaxValue) { continue }
                [void]$linesOut.Add([PSCustomObject]@{
                    text = $ln.Text
                    x = [int]([math]::Round($minX / $Scale))
                    y = [int]([math]::Round($minY / $Scale))
                    w = [int]([math]::Round(($maxX - $minX) / $Scale))
                    h = [int]([math]::Round(($maxY - $minY) / $Scale))
                })
            }
            Write-Output (@{ lines = $linesOut } | ConvertTo-Json -Compress -Depth 3)
        } catch {
            Write-Output (@{ lines = @(); error = $_.Exception.Message } | ConvertTo-Json -Compress -Depth 3)
        }
        [Console]::Out.Flush()
    }
} catch {
    Write-Output (@{ lines = @(); error = $_.Exception.Message } | ConvertTo-Json -Compress)
    [Console]::Out.Flush()
}
