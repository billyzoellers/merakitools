"""
merakitools - mx.py
Billy Zoellers

CLI tools for managing Meraki networks based on Typer
"""
from typing import List, Optional
import typer
from merakitools.console import console
from merakitools.dashboardapi import dashboard, APIError
from merakitools.meraki_helpers import find_network_by_name, find_orgs_by_name, find_org_by_name
from merakitools.formatting_helpers import table_with_columns, table_mx_onetoone_nat
from merakitools.types import ProductType, MXInternetUplinks
from rich.prompt import Confirm
from rich import inspect
import ipaddress

app = typer.Typer()

@app.command()
def add_staticroute(
  organization_name: str,
  network_name: str,
  route: Optional[List[str]] = None,
  default_next_hop: Optional[str] = None
):
  """
  Add one or more static routes to an MX device
  """
  if not route:
    console.print(f"No routes provided.")
    raise typer.Abort()

  # Find the network
  net = find_network_by_name(organization_name, network_name)

  # Iterate through each provided route
  for route in route:
    args = len(route.split("!"))
    # Each route item should be formatted as 'routeCIDR' OR 'routeName!routeCIDR' or 'routeName!routeCIDR!routeNextHop'
    if not 0 < args < 4:
      console.print("Incorect --route formatting")
      raise typer.Abort()
    
    if args == 3:
      # 'routeName!routeCIDR!routeNextHop' format
      route_name, route_subnet, route_nexthop = route.split("!")
    elif default_next_hop:
      route_nexthop = default_next_hop
      if args == 1:
        # 'routeCIDR' format
        route_name = route.replace(".","-").replace("/","-")
        route_subnet = route
      elif args == 2:
        # 'routeName!routeCIDR' format
        route_name, route_subnet = route.split("!")
    else:
      console.print("A next hop must be provided for each route, or --default-next-hop specified")
      raise typer.Abort

    # Check route_subnet and route_nexthop validity
    try:
      route_subnet = ipaddress.ip_network(route_subnet)
    except ValueError as err:
      console.print(f"Invalid next hop IP {route_subnet}. {err}")
      raise typer.Abort()

    try:
      route_nexthop = ipaddress.ip_address(route_nexthop)
    except ValueError as err:
      console.print(f"Invalid next hop IP {route_nexthop}. {err}")
      raise typer.Abort()

    # Add route via API
    with console.status("Accessing API..."):
      try:
        dashboard.appliance.createNetworkApplianceStaticRoute(
          networkId=net["id"],
          name=route_name,
          subnet=format(route_subnet),
          gatewayIp=format(route_nexthop)
        )
      except APIError as err:
        console.print(f"Failed to create route [bold]{route_name}[/bold]:")
        for e in err.message["errors"]:
          console.print(f" - {e}")

@app.command()
def create_staticnat(
  organization_name: str,
  network_name: str,
  nat: Optional[List[str]] = None,
  port: Optional[List[str]] = None,
  uplink: MXInternetUplinks = MXInternetUplinks.one,
  confirm: bool = True
):
  """
  Add a 1:1 NAT entry to an MX device
  """
  if not nat:
    console.print(f"No NATs provided.")
    raise typer.Abort()

  # Get a copy of existing NAT rules
  net = find_network_by_name(organization_name, network_name)
  rules = dashboard.appliance.getNetworkApplianceFirewallOneToOneNatRules(net["id"])["rules"]

  # Display a table of existing rules
  table = table_mx_onetoone_nat(rules, title="Existing 1:1 NAT Rules")
  console.print(table)

  # Iterate through each provided port
  allowed_ports = []
  for p in port:
    args = len(p.split("!"))
    # Each port item should  be formatted as 'protocol!portNum' or 'protocol!portNum!allowedIPs'
    if not 1 < args < 4:
      console.print("Incorect --port formatting")
      raise typer.Abort()

    if args == 3:
      proto, num, allowed_ips = p.split("!")
    elif args == 2:
      proto, num = p.split("!")
      allowed_ips = 'any'

    # Validate protocol input
    valid_protocols = ['tcp', 'udp', 'icmp', 'any']
    if not proto in valid_protocols:
      console.print(f"Incorect --port protocol. Protocol must be \[{', '.join(valid_protocols)}]")
      raise typer.Abort()

    # Validate allowed IP input
    for ip in allowed_ips.split(","):
      if not ip == "any":
        try:
          ip = ipaddress.ip_network(ip)
        except ValueError as err:
          console.print(f"Invalid allowed IP {ip}. {err}")
          raise typer.Abort()

    # Add to list of allowed_ports for all NAT entries
    allowed_ports.append({
      "protocol": proto,
      "destinationPorts": num.split(","),
      "allowedIps": allowed_ips.split(",")
    })

  # Iterate through each provided NAT
  new_rules = []
  for entry in nat:
    args = len(entry.split("!"))
    # Each NAT item should be formatted as 'publicIP!privateIP' or 'natName!publicIP!privateIP'
    if not 1 < args < 4:
      console.print("Incorect --nat formatting")
      raise typer.Abort()

    if args == 3:
      # 'natName!publicIP!privateIP' format
      name, public_ip, private_ip = entry.split("!")
    elif args == 2:
      # 'publicIP!privateIP' format
      public_ip, private_ip = entry.split("!")
      name = public_ip.replace('.', '-')

    # Validate public IP input
    try:
      public_ip = ipaddress.ip_address(public_ip)
    except ValueError as err:
      console.print(f"Invalid public IP {public_ip}. {err}")
      raise typer.Abort()

    # Validate private IP input
    try:
      private_ip = ipaddress.ip_address(private_ip)
    except ValueError as err:
      console.print(f"Invalid private IP {private_ip}. {err}")
      raise typer.Abort()

    # Add to list of new_rules
    new_rules.append({
      "name": name,
      "lanIp": format(private_ip),
      "publicIp": format(public_ip),
      "uplink": uplink.value,
      "allowedInbound": allowed_ports
    })

  # Display a table of new rules
  table = table_mx_onetoone_nat(new_rules, title="New 1:1 NAT Rules", style="green")
  console.print(table)

  # Confirm before adding changes
  if confirm:
    request_confirm = Confirm.ask("Do you want to continue adding new rules?", console=console)
    if not request_confirm:
      console.print('Rules not added')
      raise typer.Abort()
  
  rules += new_rules
  dashboard.appliance.updateNetworkApplianceFirewallOneToOneNatRules(net['id'], rules)