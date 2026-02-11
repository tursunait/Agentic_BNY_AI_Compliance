import json
from typing import Dict

from crewai import Crew, Process, LLM

from backend.tools.kb_tools import (
    search_kb_tool,
    get_schema_tool,
    get_validation_rules_tool,
    convert_to_narrative_tool,
    get_field_mappings_tool,
    add_to_kb_tool,
)
from backend.tools.web_scraper import scrape_fincen_tool, download_regulation_tool
from backend.tools.pdf_tools import fill_pdf_form_tool, generate_report_pdf_tool
from backend.config.settings import settings
from backend.agents.router_agent import create_router_agent, create_router_task
from backend.agents.researcher_agent import create_researcher_agent, create_researcher_task
from backend.agents.aggregator_agent import create_aggregator_agent, create_aggregator_task
from backend.agents.narrative_agent import create_narrative_agent, create_narrative_task
from backend.agents.validator_agent import create_validator_agent, create_validator_task
from backend.agents.filer_agent import create_filer_agent, create_filer_task


def create_compliance_crew(transaction_data: dict) -> Dict[str, dict]:
    base_llm = LLM(model="gpt-4.1", temperature=0.1, max_tokens=4000, api_key=settings.OPENAI_API_KEY)
    narrative_llm = LLM(model="gpt-4.1", temperature=0.4, max_tokens=2000, api_key=settings.OPENAI_API_KEY)

    router_agent = create_router_agent(
        llm=base_llm,
        tools=[search_kb_tool, get_schema_tool, convert_to_narrative_tool],
    )
    router_task = create_router_task(router_agent, transaction_data)
    router_crew = Crew(agents=[router_agent], tasks=[router_task], process=Process.sequential, verbose=True)

    router_result = router_crew.kickoff()
    router_output = json.loads(router_result)

    if router_output.get("kb_status") == "MISSING":
        researcher_agent = create_researcher_agent(llm=base_llm, tools=[scrape_fincen_tool, download_regulation_tool, add_to_kb_tool])
        researcher_task = create_researcher_task(researcher_agent, router_output)
        researcher_crew = Crew(agents=[researcher_agent], tasks=[researcher_task], process=Process.sequential, verbose=True)
        researcher_crew.kickoff()

    aggregator_agent = create_aggregator_agent(
        llm=base_llm,
        tools=[get_schema_tool, get_field_mappings_tool, search_kb_tool, get_validation_rules_tool],
    )
    narrative_agent = create_narrative_agent(llm=narrative_llm, tools=[search_kb_tool])
    validator_agent = create_validator_agent(llm=base_llm, tools=[get_validation_rules_tool, search_kb_tool])

    aggregator_task = create_aggregator_task(aggregator_agent, router_output)
    narrative_task = create_narrative_task(narrative_agent, {})
    validator_task = create_validator_task(validator_agent, {}, {})

    main_crew = Crew(
        agents=[aggregator_agent, narrative_agent, validator_agent],
        tasks=[aggregator_task, narrative_task, validator_task],
        process=Process.sequential,
        verbose=True,
    )

    main_result = main_crew.kickoff()
    validation_output = json.loads(main_result)

    final_output: Dict[str, dict]
    if validation_output.get("approval_flag"):
        filer_agent = create_filer_agent(llm=base_llm, tools=[fill_pdf_form_tool, generate_report_pdf_tool])
        filer_task = create_filer_task(filer_agent, {}, {}, validation_output)
        filer_crew = Crew(agents=[filer_agent], tasks=[filer_task], process=Process.sequential, verbose=True)
        filer_result = filer_crew.kickoff()
        final_output = json.loads(filer_result)
    else:
        final_output = {
            "status": "needs_review",
            "validation_report": validation_output,
            "message": "Report did not pass validation - human review required",
        }

    return {
        "router": router_output,
        "validation": validation_output,
        "final": final_output,
    }
