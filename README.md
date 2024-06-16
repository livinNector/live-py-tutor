# Python Tutor Live progamming Mode - Backed by Pyodide

This project is a modified version of Python tutor live programming mode by @pgbovine and inspired by https://cs320.cs.wisc.edu/pythontutor/live.html#mode=edit in the usage of pyodide.

## Features of Python Tutor
- Step by step visualization.
- Visualize different data structures like `list`, `tuple`, `dict` and `set`.
- Good debugging interface and execution stops at the error.
- Move back and forth through the execution.
- Different pointer modes like inline primitives, nested object, render everything in heap

## Live Programming mode
In the live programming mode the outputs are computed as you type

## Perks of Pyodide(powered by wasm)
- Everthing runs on your browser
- Lower latency and faster execution.

## Additional features
- Added support for a input cell for standard input.
- Modified support for permanent link generation(sharable) - added inputs to be also encoded with the permanent link

## Examples
- [loops and input](https://livinnector.github.io/live-py-tutor/#code=%0Afor%20i%20in%20range%2810%29%3A%0A%20%20%20%20print%28i,i**2%29%20%23%20try%20changing%20the%20value%20here%0A%20%20%20%20%0A%23%20go%20step%20by%20step%20with%20the%20slider%20or%20control%20buttons%0Atotal%20%3D%200%0Anum%20%3D%20int%28input%28%29%29%0Awhile%20num%20!%3D0%3A%0A%20%20%20%20total%2B%3Dnum%0A%20%20%20%20num%20%3D%20int%28input%28%29%29%0Aprint%28%22Total%22,total%29&cumulative=false&curInstr=37&heapPrimitives=nevernest&mode=edit&origin=opt-live.js&py=3&rawInputLstJSON=%5B%223%22,%224%22,%225%22,%226%22,%220%22%5D&textReferences=false)
- [collections](https://livinnector.github.io/live-py-tutor/#code=fruits%20%3D%20%5B%22apple%22,%22orange%22,%20%22mango%22,%20%22banana%22%5D%0Afor%20i%20in%20range%28len%28fruits%29%29%3A%0A%20%20%20%20fruits%5Bi%5D%20%3D%20fruits%5Bi%5D.title%28%29%0A%0An%20%3D%20int%28input%28%29%29%0Afor%20i%20in%20range%28n%29%3A%0A%20%20%20%20fruits.append%28input%28%29%29%0A%0Afruit_chars%20%3D%20%7Bchar%20for%20fruit%20in%20fruits%20for%20char%20in%20fruit%20%7D%0Afruit_chars_lower%20%3D%20%7Bchar.lower%28%29%20for%20fruit%20in%20fruits%20for%20char%20in%20fruit%20%7D%0A%0Afruit_char_lens%20%3D%20%7B%7D%0Afor%20fruit%20in%20fruits%3A%0A%20%20%20%20fruit_char_lens%5Bfruit%5D%20%3D%20len%28fruit%29&cumulative=false&curInstr=132&heapPrimitives=nevernest&mode=edit&origin=opt-live.js&py=3&rawInputLstJSON=%5B%223%22,%22Kiwi%22,%22Grapes%22,%22Cherry%22,%22%22%5D&textReferences=false)
- [recursion](https://livinnector.github.io/live-py-tutor/#code=def%20factorial%28n%29%3A%0A%20%20%20%20if%20n%20%3C%3D1%3A%0A%20%20%20%20%20%20%20%20return%201%0A%20%20%20%20return%20n*factorial%28n-1%29%0A%20%20%20%20%0Aanswer%20%3D%20factorial%2810%29&cumulative=false&curInstr=42&heapPrimitives=nevernest&mode=edit&origin=opt-live.js&py=3&rawInputLstJSON=%5B%5D&textReferences=false)
