import re
import string
from collections import Counter

def generate_alias_candidates(exclude_set, limit=5000):
    """
    Generates a list of sorted, deterministic alias candidates (letters only)
    excluding any strings present in exclude_set.
    """
    candidates = []

    # 2-letter uppercase combinations (AA - ZZ)
    for c1 in string.ascii_uppercase:
        for c2 in string.ascii_uppercase:
            cand = c1 + c2
            if cand not in exclude_set:
                candidates.append(cand)
                if len(candidates) >= limit:
                    return candidates

    # 2-letter lowercase combinations (aa - zz)
    for c1 in string.ascii_lowercase:
        for c2 in string.ascii_lowercase:
            cand = c1 + c2
            if cand not in exclude_set:
                candidates.append(cand)
                if len(candidates) >= limit:
                    return candidates

    # 3-letter uppercase combinations (AAA - ZZZ)
    for c1 in string.ascii_uppercase:
        for c2 in string.ascii_uppercase:
            for c3 in string.ascii_uppercase:
                cand = c1 + c2 + c3
                if cand not in exclude_set:
                    candidates.append(cand)
                    if len(candidates) >= limit:
                        return candidates

    return candidates

def find_candidate_words(text):
    """
    Finds words of length >= 6 (only letters and underscores) that could be dictionary keys.
    """
    return re.findall(r'\b[a-zA-Z_]{6,}\b', text)

def build_file_dictionary(text, protected_manager, token_counter, min_margin=5):
    """
    Builds a local dictionary for a single file.
    Only includes mappings that yield a net positive token gain.
    """
    protected_text = protected_manager.protect(text)
    exclude_set = set(re.findall(r'\b\w+\b', text))

    candidate_words = find_candidate_words(protected_text)
    if not candidate_words:
        return {}, ""

    word_counts = Counter(candidate_words)

    aliases = generate_alias_candidates(exclude_set, limit=len(word_counts) + 10)

    candidates_with_gain = []
    alias_idx = 0
    sorted_words = sorted(word_counts.items(), key=lambda x: x[1] * len(x[0]), reverse=True)

    dict_overhead = token_counter.count("> 🤖 AI RAG DICT: \n\n")

    for word, freq in sorted_words:
        if freq < 2:
            continue

        if alias_idx >= len(aliases):
            break

        alias = aliases[alias_idx]

        tokens_word = token_counter.count(word)
        tokens_alias = token_counter.count(alias)

        gross_gain = freq * (tokens_word - tokens_alias)
        entry_cost = token_counter.count(f"{alias}={word}, ")

        net_word_gain = gross_gain - entry_cost
        if net_word_gain > 0:
            candidates_with_gain.append((word, alias, net_word_gain))
            alias_idx += 1

    if not candidates_with_gain:
        return {}, ""

    total_gain = sum(g for _, _, g in candidates_with_gain)
    if total_gain <= (dict_overhead + min_margin):
        return {}, ""

    final_dict = {word: alias for word, alias, _ in candidates_with_gain}
    rag_str = ", ".join(f"{alias}={word}" for word, alias in final_dict.items())
    header = f"> 🤖 AI RAG DICT: {rag_str}\n\n"

    return final_dict, header

def apply_dictionary(text, dictionary, protected_manager):
    """
    Applies the dictionary to the text, keeping protected regions safe.
    """
    if not dictionary:
        return text

    protected_text = protected_manager.protect(text)
    sorted_dict = sorted(dictionary.items(), key=lambda x: len(x[0]), reverse=True)
    for word, alias in sorted_dict:
        pattern = re.compile(rf'\b{re.escape(word)}\b')
        protected_text = pattern.sub(alias, protected_text)

    return protected_manager.restore(protected_text)

def build_corpus_dictionary(all_files_content, token_counter, min_margin=5):
    """
    Builds a corpus-level dictionary.
    """
    exclude_set = set()
    for text in all_files_content:
        exclude_set.update(re.findall(r'\b\w+\b', text))

    word_counts = Counter()
    from cida.markdown.protected_regions import ProtectedRegionsManager
    for text in all_files_content:
        pm = ProtectedRegionsManager()
        protected = pm.protect(text)
        candidate_words = find_candidate_words(protected)
        word_counts.update(candidate_words)

    aliases = generate_alias_candidates(exclude_set, limit=len(word_counts) + 10)

    candidates_with_gain = []
    alias_idx = 0
    sorted_words = sorted(word_counts.items(), key=lambda x: x[1] * len(x[0]), reverse=True)

    for word, freq in sorted_words:
        if freq < 3:
            continue
        if alias_idx >= len(aliases):
            break
        alias = aliases[alias_idx]
        tokens_word = token_counter.count(word)
        tokens_alias = token_counter.count(alias)
        gross_gain = freq * (tokens_word - tokens_alias)
        entry_cost = token_counter.count(f"{alias}={word}\n")

        if gross_gain - entry_cost > 0:
            candidates_with_gain.append((word, alias, gross_gain - entry_cost))
            alias_idx += 1

    total_gain = sum(g for _, _, g in candidates_with_gain)
    if total_gain <= min_margin:
        return {}

    return {word: alias for word, alias, _ in candidates_with_gain}


class CorpusDictionaryBuilder:
    """Adapter implementing DictionaryBuilder protocol for corpus-level dictionary generation."""

    def build_corpus_dictionary(self, all_files_content: list, token_counter, min_margin: int = 5) -> dict:
        """
        Builds a corpus-level dictionary.
        Delegates to the standalone function for backward compatibility.
        """
        return build_corpus_dictionary(all_files_content, token_counter, min_margin)
