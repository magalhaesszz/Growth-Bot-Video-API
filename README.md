# Growth Bot Video API

API FastAPI/FFmpeg usada pelo Growth Bot para aplicar fundo 9:16, remover bordas pretas ou brancas, gerar preview e editar posição e tamanho por mouse ou toque.

No Railway, configure `API_SECRET` com o mesmo valor usado como `VIDEO_API_SECRET` no bot. O serviço não inicia sem essa variável.

Endpoints principais ficam em `/api/v1`: fundo, processamento individual, lote, preview e editor visual. Rotas privadas exigem o cabeçalho `x-api-secret`.

O deploy usa o `Dockerfile`, que instala o FFmpeg. Para verificar o código localmente:

```text
python -m compileall -q .
python -m unittest discover -s tests -v
```
