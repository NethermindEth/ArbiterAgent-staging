from typing import Optional, Dict, Any
from app.config import config
from langchain_anthropic import ChatAnthropic
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain


"""
Claude model configuration and initialization module.
Provides functions to create and configure Claude models with parameters from environment variables.
"""

def get_model_config() -> Dict[str, Any]:
    """
    Get Claude model configuration from environment variables.
    
    Returns:
        Dictionary with model configuration parameters
    """
    return {
        "model_name": config.claude_model,
        "temperature": config.claude_temperature,
        "max_tokens": config.claude_max_tokens,
        "anthropic_api_key": config.claude_api_key
    }

def create_claude_model(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None
) -> ChatAnthropic:
    """
    Create a Claude model instance with specified parameters or from environment variables.
    
    Args:
        model_name: Optional model name override
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
        api_key: Optional API key override
        
    Returns:
        Configured ChatAnthropic instance
        
    Raises:
        ValueError: If API key is not provided and not in environment variables
    """
    # Get config from environment variables
    config = get_model_config()
    
    # Override with any provided parameters
    if model_name:
        config["model_name"] = model_name
    if temperature is not None:
        config["temperature"] = temperature
    if max_tokens:
        config["max_tokens"] = max_tokens
    if api_key:
        config["anthropic_api_key"] = api_key
        
    # Validate API key
    if not config["anthropic_api_key"]:
        raise ValueError("CLAUDE_API_KEY environment variable is not set")
    
    # Return configured model
    return ChatAnthropic(
        model=config["model_name"],
        temperature=config["temperature"],
        max_tokens_to_sample=config["max_tokens"],
        anthropic_api_key=config["anthropic_api_key"]
    )

def create_similarity_chain(
    model: Optional[ChatAnthropic] = None,
    prompt_template: Optional[str] = None
) -> LLMChain:
    """
    Create a LangChain for similarity comparison.
    
    Args:
        model: Optional ChatAnthropic model (created from environment if not provided)
        prompt_template: Optional custom prompt template
        
    Returns:
        Configured LLMChain for similarity comparison
    """
    # Create model if not provided
    if not model:
        model = create_claude_model()
    
    # Use default similarity prompt if not provided
    if not prompt_template:
        prompt_template = """
        Compare these two security findings and determine their similarity on a scale from 0 to 1.

        Finding 1:
        {finding1}

        Finding 2:
        {finding2}

        Analyze the similarity in these aspects:
        1. Title similarity (0.25 weight)
        2. Description similarity (0.35 weight)
        3. Vulnerability type (0.25 weight)
        4. File path and code references (0.15 weight)

        For two findings to be considered similar, they should describe the same underlying security issue.
        Even if the descriptions are somewhat similar but they point to different vulnerabilities, they should receive a low similarity score.

        First explain your comparison in 2-3 sentences, then output a single decimal number between 0 and 1 on a separate line.
        Format your final answer as: "Similarity score: 0.XX"
        """
    
    # Create prompt
    prompt = PromptTemplate(
        input_variables=["finding1", "finding2"],
        template=prompt_template
    )
    
    # Create and return chain
    return LLMChain(llm=model, prompt=prompt, output_key="similarity") 