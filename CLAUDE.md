# DataHuddle Fantasy Football Application
This application is a Streamlit application for aggregating various data sources and creating tools, visualizations, and analytical views for fantasy football: specifically for Daily Fantasy and Regular (Season Long) Fantasy (both pre-draft tools and in-season tools).


To run the application, we use the following command:
```
    streamlit run streamlit_app.py
```

To run the test suite:
```
    pytest
```

## Documentation Style
Every function, method, and class must have a docstring. Use Google-style formatting. Write for a beginner programer - someone who knows basic Python syntax but not this codebase.

### Required in every docstring
1. **One-line summary**: what this does, in plain English. No jargon. If a technical term is unavoidable, describe it in the description.
2. **Description**: 1-3 sentences on *why* this exists and when it gets used.
3. **Steps**: a numbered, plain-English walkthrough of what happens inside. Whenever a step calls another function in this project, name it and say in a few words what it's for.
4. **Args / Returns / Raises**: describe each in beginner terms, not just types.

### Inline Comment Style
Include inline comments as you see fit. Typically, keep these as concise and to the point as possible, in terms a beginning programmer can understand. Scenarios often applicable for inline comments:
1. IMPORTANT: Whenever you use a function to get data, describe the form of that data
2. Separation of components within a UI
3. If there is a non-intuitive Python call of some sort, explain what it does
4. Utilize to breakdown the steps of an advanced sequence

# Response Instructions

## Code Planning + Answering Questions
I will often ask for help in planning code, ask questions about existing code, or ask for help in implementing something.

**IMPORTANT**: Before evaluating the problem and answering the question, do these two steps:
1) Ask me any clarifying questions or unclear design decisions
2) Ask me if I want to make the changes myself OR if I want you to make them. If I want to make them myself, write out steps to make the change (with full code snippets) inline.

When answering questions in which I ask for code / implementations, please write out the code in order of what I should implement (in steps), augmented with concise but comprehensive commenting tailored toward helping a beginner programmer understand what the code acheives. For any code that loads any sort of data (i.e. a DataFrame), provide a line of commenting with a generalized detailing of the structure of that data.

