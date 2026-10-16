---
sample: fictional note for testing, not real
tags: [ml, ai]
---
# RAG basics

RAG means retrieval-augmented generation: first find the right text, then let the language model answer from it.

Steps: cut documents into chunks of about 500 to 1500 characters, turn each chunk into an embedding, store them in a vector database, and search by meaning.

Always show the sources, so people can check the answer. If nothing relevant is found, the assistant should say so instead of guessing.
