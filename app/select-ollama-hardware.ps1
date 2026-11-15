param([Parameter(Mandatory)] [string]$Model, [string]$BaseUrl = 'http://127.0.0.1:11434')

# Run against an isolated Ollama server. Compare warm prompt processing and generation,
# using the same context and prompt in both modes. Failure always selects CPU.
try {
    $measurements = @{}
    foreach ($mode in @('CPU', 'GPU')) {
        $layers = if ($mode -eq 'CPU') { 0 } else { -1 }
        $request = @{
            model = $Model
            stream = $false; think = $false
            options = @{ num_gpu = $layers; num_ctx = 16384; num_predict = 32; temperature = 0 }
        }
        # The first request loads the model. Measure a second, warm request.
        # Unique prefixes prevent Ollama from reusing cached prompt evaluation.
        $request.prompt = "$(New-Guid) Explain briefly how a local health monitor can summarize a sensor reading."
        $body = $request | ConvertTo-Json -Depth 4 -Compress
        $null = Invoke-RestMethod -Uri "$BaseUrl/api/generate" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 300
        $request.prompt = "$(New-Guid) Explain briefly how a local health monitor can summarize a sensor reading."
        $body = $request | ConvertTo-Json -Depth 4 -Compress
        $result = Invoke-RestMethod -Uri "$BaseUrl/api/generate" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 300
        if ($result.eval_count -le 0 -or $result.prompt_eval_duration -le 0 -or $result.eval_duration -le 0) { throw 'Invalid timing' }
        $measurements[$mode] = [double]$result.prompt_eval_duration + 32 * [double]$result.eval_duration / [double]$result.eval_count
        if ($mode -eq 'GPU') {
            $running = Invoke-RestMethod -Uri "$BaseUrl/api/ps" -TimeoutSec 10
            $loaded = @($running.models | Where-Object { $_.name -eq $Model })
            if ($loaded.Count -ne 1 -or $loaded[0].size_vram -le 0) { throw 'GPU not used' }
        }
    }
    if ($measurements.GPU -lt $measurements.CPU) { 'GPU' } else { 'CPU' }
} catch {
    'CPU'
}
