 now that phase two implementation is in progress, let's try to draft the thoughts for the agent, and then we would build a high-level architecture. Then, what are things that are needed in the agent? We'll start trying to craft that. The main purpose of this exercise is to create a document that drafts all our needs, okay? 

From the backend, what we would be getting to our AI layer is primarily the user question, and we would also be getting, if needed, the conversation ID.
Now, what we need to do is, as you know, an agent is a model plus a harness. In this agent, whatever we are trying to build, our focus should be on building this harness. I want to build this harness as part of modeling. Right now, I want to build an LLM proxy. See here: I don't want to choose one model here. Primarily, users will have a say on what models they want to choose, and from the frontend and from the backend, we would get what model the user is requesting.
This model layer, whatever we want, I want it to be like a gateway. If the user selects Sonnet, I want to pass this question to the Sonnet model. If the user selects GPT, I want to pass this question to GPT. This model layer in the agent is equal to model plus harness. This model layer, I want it to be multi. I want support to provide, to take, or to have multiple providers. This just has to be like a gateway. Whatever the user selects, we want to send that to that specific model. That's about the model.
Let's talk about the harness route. This has to be the key element inside this. The first thing is the system prompt. Inside the harness, I want the system prompt. I want you to craft a system prompt, as you know, because we are building this for the customer support agent.
The system prompt has to have all the things, like:
- objective
- tone
- what the guidelines are
- what the escalation rules are
- all this stuff
In my system prompt, I want my tone to be formal, addressing that it knows its limitations or knows what the in-scope things are that it has to address. Guidelines and rules have all those things in my system prompt.
Whenever we are building a system prompt, the system prompt is being passed for every LLM call. Make sure that you build the system prompt with, let's say, < 0.5% of the entire context window. Make sure you have as many fewer tokens as you can, but don't try to compromise on the number of tokens that you are passing.
That's the first step in the harness. The second step is building the tools. This is the key aspect because, as you know, in the harness, we would be passing all the tool schemas, or tool definition, tool name, tool description, and all this information every call.
I want you to look into the API prioritization document that we have created in the docs folder. It lets you know what APIs are there, and we need to create the tools for all those APIs. Primarily, I want you to prioritize building tools on that. It is suggesting you build 12 tools. Make sure you validate it and then create the 12 tools or 13 tools that are needed, and build the tool inventory as well, such that we can give this tool inventory in every LLM call. That is the second aspect.

In the above, I talked about one set of tools, which is taking the APIs and converting the tools. I need to talk about the second set of tools, which is the RAG tool now.

I need to build a retrieval tool and then attach that retrieval to the agent as well, such that whenever the agent needs this information or whenever the agent needs any policy information, it can go and retrieve the relevant information from the vector database.

For that, I want you to have a code build such that we can do the ingestion pipeline, which runs once, and then store that information into the vector database. We also need to create a tool, which is called the retrieval tool, such that whenever the agent needs that, it can send the question and then retrieve the relevant information about that question as well. This is one set of tools that we also need to build. 

Okay, the third thing is to build the loop. Fundamentally, inside the harness, the third most important thing is the loop. I want you to build the loop: the loop exit criteria.
Loop exit criteria:
- We would follow a React framework here, where we would have reasoning, act, and observe. This has to happen in a loop. Whatever the user question is, we would send this user question into this framework. It uses the harness, and then we would take and call the LLM regularly from the model provider.
- Whenever the task is done, whenever the LLM feels the task is done, that is where we would exit the loop.
- The limit as well. Let's say if the LLM is in a loop and it is looping for much time, and it is going, let's say, in an infinite loop, we don't want to do that. I only want, at this point in time, to iterate 10 times. If this loop is going beyond 10 times, I want to break at that point in time and say that I'm not going to go further. Then give some nice message to the user saying that something went wrong inside the agent, but my max looping limit here is 10.