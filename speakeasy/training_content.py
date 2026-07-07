"""Pre-generated read-aloud training content (static, offline).

Each session is a handful of short prompts the user reads aloud. A prompt's
`text` is what they read; `targets` are the specific words/phrases we score
and may save corrections for — filler words give natural context but are
never turned into corrections, so reading a sentence can't clobber an
everyday word. Sessions are ordered easy → hard: everyday phrases first
(calibration), then the words the on-device model most often mishears.
"""

SESSIONS = [
    {
        "name": "Everyday phrases",
        "prompts": [
            {"text": "Good morning, how are you today?", "targets": ["morning"]},
            {"text": "Please send me the email later.", "targets": ["email"]},
            {"text": "Let's schedule a meeting for tomorrow.", "targets": ["schedule"]},
            {"text": "Thanks so much, I really appreciate it.", "targets": ["appreciate"]},
            {"text": "Can you follow up on that this afternoon?", "targets": ["afternoon"]},
            {"text": "I'll get back to you as soon as possible.", "targets": ["possible"]},
        ],
    },
    {
        "name": "Tech & jargon",
        "prompts": [
            {"text": "Deploy the service to Kubernetes.", "targets": ["Kubernetes"]},
            {"text": "The database runs on PostgreSQL.", "targets": ["PostgreSQL"]},
            {"text": "Authenticate the request with OAuth.", "targets": ["OAuth"]},
            {"text": "Send the payload as JSON over HTTPS.", "targets": ["JSON", "HTTPS"]},
            {"text": "Restart the nginx reverse proxy.", "targets": ["nginx"]},
            {"text": "Merge the pull request into the main branch.", "targets": ["branch"]},
            {"text": "Run the async function inside the coroutine.", "targets": ["async", "coroutine"]},
        ],
    },
    {
        "name": "Names & proper nouns",
        "prompts": [
            {"text": "I use Speakeasy for dictation.", "targets": ["Speakeasy"]},
            {"text": "The model is called Parakeet.", "targets": ["Parakeet"]},
            {"text": "It's a clone of Wispr Flow.", "targets": ["Wispr Flow"]},
            {"text": "Ask Claude to summarize the notes.", "targets": ["Claude"]},
            {"text": "It runs on Apple MLX.", "targets": ["MLX"]},
            {"text": "Anthropic builds these models.", "targets": ["Anthropic"]},
        ],
    },
    {
        "name": "Numbers & units",
        "prompts": [
            {"text": "The file is about six hundred megabytes.", "targets": ["megabytes"]},
            {"text": "Sample the audio at sixteen kilohertz.", "targets": ["kilohertz"]},
            {"text": "It responds in roughly one second.", "targets": ["second"]},
            {"text": "Wait twenty-five milliseconds before retrying.", "targets": ["milliseconds"]},
            {"text": "The meeting is at three thirty p.m.", "targets": ["thirty"]},
            {"text": "That's ninety-nine percent accurate.", "targets": ["percent"]},
        ],
    },
    {
        "name": "Tricky words & homophones",
        "prompts": [
            {"text": "The algorithm converges quickly.", "targets": ["algorithm"]},
            {"text": "Check the cache before the query.", "targets": ["cache"]},
            {"text": "The latency is negligible.", "targets": ["latency", "negligible"]},
            {"text": "Prioritize the queue by severity.", "targets": ["queue", "severity"]},
            {"text": "The paradigm is asynchronous.", "targets": ["paradigm", "asynchronous"]},
            {"text": "Its behaviour is deterministic.", "targets": ["deterministic"]},
        ],
    },
]


# The most frequent English words. Used as a safety guard: a correction is
# never saved when the *heard* form is entirely common words, so a normal
# word like "to" or "communities" can't be rewritten by a stray take.
COMMON_WORDS = {
    "a", "about", "above", "after", "again", "all", "am", "an", "and", "any",
    "are", "as", "at", "back", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "call", "came", "can", "come",
    "could", "day", "did", "do", "does", "doing", "done", "down", "each",
    "even", "every", "few", "find", "first", "for", "from", "get", "give",
    "go", "going", "good", "got", "great", "had", "has", "have", "having",
    "he", "her", "here", "hers", "herself", "him", "himself", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "itself", "just", "keep",
    "know", "last", "let", "life", "like", "little", "long", "look", "made",
    "make", "man", "many", "may", "me", "might", "mine", "more", "most",
    "much", "must", "my", "myself", "need", "never", "new", "next", "no",
    "not", "now", "of", "off", "old", "on", "once", "one", "only", "or",
    "other", "our", "ours", "out", "over", "own", "part", "people", "put",
    "really", "right", "said", "same", "say", "see", "she", "should", "since",
    "so", "some", "still", "such", "take", "than", "that", "the", "their",
    "theirs", "them", "then", "there", "these", "they", "thing", "think",
    "this", "those", "through", "time", "to", "too", "two", "under", "up",
    "upon", "us", "use", "used", "very", "want", "was", "way", "we", "well",
    "were", "what", "when", "where", "which", "while", "who", "why", "will",
    "with", "would", "year", "you", "your", "yours", "yourself",
}
