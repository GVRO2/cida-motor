def is_optimization_beneficial(original_tokens: int, minified_tokens: int, sidecar_tokens: int, auxiliary_tokens: int) -> bool:
    """
    Decides if the token optimization is beneficial by calculating net savings.
    Otimização é benéfica apenas quando a economia líquida é estritamente maior que 0.
    """
    gross_savings = original_tokens - minified_tokens
    overhead = sidecar_tokens + auxiliary_tokens
    return (gross_savings - overhead) > 0
