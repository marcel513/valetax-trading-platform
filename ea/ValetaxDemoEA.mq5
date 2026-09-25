// Demo-only coordinator EA. Live accounts are rejected in OnInit and OnTimer.
#property strict
#property version "0.1"
#include <Trade/Trade.mqh>

input string ServerBaseUrl = "https://your-server.example";
input string OneTimeLinkCode = "";
input bool LocalPause = true;
input ulong PlatformMagic = 90025001;

CTrade trade;
string eaToken = "";
string goldSymbol = "";
bool serverEnabled = false;
double configuredLot = 0.01;
int maxPositions = 1;
double dailyLossLimit = 20.0;
int maxSpreadPoints = 50;

string Field(string json, string key)
{
   string needle = "\"" + key + "\":";
   int p = StringFind(json, needle);
   if(p < 0) return "";
   p += StringLen(needle);
   while(StringSubstr(json,p,1)==" ") p++;
   bool quoted = StringSubstr(json,p,1)=="\"";
   if(quoted) p++;
   int q=p;
   while(q<StringLen(json))
   {
      string c=StringSubstr(json,q,1);
      if((quoted && c=="\"") || (!quoted && (c=="," || c=="}" || c=="]"))) break;
      q++;
   }
   return StringSubstr(json,p,q-p);
}

string Esc(string s)
{
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   return s;
}

bool Request(string method,string path,string payload,string &answer,bool authenticated=true)
{
   char data[],result[];
   string responseHeaders;
   string headers="Content-Type: application/json\r\n";
   if(authenticated) headers+="Authorization: Bearer "+eaToken+"\r\n";
   StringToCharArray(payload,data,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(data)>0) ArrayResize(data,ArraySize(data)-1);
   ResetLastError();
   int status=WebRequest(method,ServerBaseUrl+path,headers,10000,data,result,responseHeaders);
   answer=CharArrayToString(result,0,WHOLE_ARRAY,CP_UTF8);
   if(status<200 || status>=300)
   {
      Print("Coordinator request failed: HTTP ",status," MT5 error ",GetLastError());
      return false;
   }
   return true;
}

bool Demo()
{
   return AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
}

string TokenFile()
{
   return "ValetaxDemo_"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+".txt";
}

void SaveToken()
{
   int h=FileOpen(TokenFile(),FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h!=INVALID_HANDLE) { FileWriteString(h,eaToken); FileClose(h); }
}

void LoadToken()
{
   int h=FileOpen(TokenFile(),FILE_READ|FILE_TXT|FILE_COMMON);
   if(h!=INVALID_HANDLE) { eaToken=FileReadString(h); FileClose(h); }
}

string DetectGold()
{
   // Broker names can have suffixes/prefixes. Inspect the actual Market Watch symbols.
   int count=SymbolsTotal(true);
   for(int i=0;i<count;i++)
   {
      string name=SymbolName(i,true);
      string upper=name;
      StringToUpper(upper);
      if(StringFind(upper,"XAU")>=0 && StringFind(upper,"USD")>=0)
         return name;
   }
   return "";
}

bool Link()
{
   if(OneTimeLinkCode=="") return false;
   string json="{\"code\":\""+Esc(OneTimeLinkCode)+"\",\"login\":"+
      IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+",\"server\":\""+
      Esc(AccountInfoString(ACCOUNT_SERVER))+"\",\"account_type\":\"demo\",\"currency\":\""+
      Esc(AccountInfoString(ACCOUNT_CURRENCY))+"\",\"trade_allowed\":true}";
   string answer;
   if(!Request("POST","/api/ea/link",json,answer,false)) return false;
   eaToken=Field(answer,"ea_token");
   if(eaToken=="") return false;
   SaveToken();
   Print("Demo MT5 account linked. Remove OneTimeLinkCode from EA inputs now.");
   return true;
}

bool Heartbeat()
{
   goldSymbol=DetectGold();
   if(goldSymbol=="") { Print("No XAU/USD gold symbol in Market Watch"); return false; }
   double contract=SymbolInfoDouble(goldSymbol,SYMBOL_TRADE_CONTRACT_SIZE);
   double tickSize=SymbolInfoDouble(goldSymbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(goldSymbol,SYMBOL_TRADE_TICK_VALUE);
   double minVol=SymbolInfoDouble(goldSymbol,SYMBOL_VOLUME_MIN);
   double step=SymbolInfoDouble(goldSymbol,SYMBOL_VOLUME_STEP);
   double maxVol=SymbolInfoDouble(goldSymbol,SYMBOL_VOLUME_MAX);
   if(contract<=0 || tickSize<=0 || tickValue<=0 || minVol<=0 || step<=0 || maxVol<=0) return false;
   string json="{\"login\":"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+
      ",\"server\":\""+Esc(AccountInfoString(ACCOUNT_SERVER))+
      "\",\"account_type\":\"demo\",\"trade_allowed\":"+
      (AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)?"true":"false")+
      ",\"symbol\":\""+Esc(goldSymbol)+"\",\"contract_size\":"+DoubleToString(contract,4)+
      ",\"tick_size\":"+DoubleToString(tickSize,8)+",\"tick_value\":"+DoubleToString(tickValue,8)+
      ",\"volume_min\":"+DoubleToString(minVol,4)+",\"volume_step\":"+DoubleToString(step,4)+
      ",\"volume_max\":"+DoubleToString(maxVol,4)+"}";
   string answer;
   if(!Request("POST","/api/ea/heartbeat",json,answer)) return false;
   serverEnabled=(Field(answer,"enabled")=="true");
   configuredLot=StringToDouble(Field(answer,"lot"));
   maxPositions=(int)StringToInteger(Field(answer,"max_positions"));
   dailyLossLimit=StringToDouble(Field(answer,"daily_loss_limit"));
   maxSpreadPoints=(int)StringToInteger(Field(answer,"max_spread_points"));
   return Field(answer,"demo_only")=="true";
}

double TodayPlatformProfit()
{
   datetime start=StringToTime(TimeToString(TimeCurrent(),TIME_DATE));
   if(!HistorySelect(start,TimeCurrent())) return 0;
   double total=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=PlatformMagic) continue;
      total+=HistoryDealGetDouble(deal,DEAL_PROFIT)+HistoryDealGetDouble(deal,DEAL_COMMISSION)+HistoryDealGetDouble(deal,DEAL_SWAP);
   }
   return total;
}

int OpenPlatformPositions()
{
   int count=0;
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket>0 && PositionSelectByTicket(ticket) && (ulong)PositionGetInteger(POSITION_MAGIC)==PlatformMagic) count++;
   }
   return count;
}

string CheckSignal(string side,double sl,double &price)
{
   if(!Demo()) return "LIVE_ACCOUNT_BLOCKED";
   if(LocalPause || !serverEnabled) return "PAUSED_OR_SUBSCRIPTION";
   if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return "BROKER_DISCONNECTED";
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)) return "TRADE_DISABLED";
   if(SymbolInfoInteger(goldSymbol,SYMBOL_TRADE_MODE)!=SYMBOL_TRADE_MODE_FULL) return "MARKET_CLOSED_OR_RESTRICTED";
   MqlTick tick;
   if(!SymbolInfoTick(goldSymbol,tick) || tick.ask<=0 || tick.bid<=0) return "NO_QUOTES";
   double point=SymbolInfoDouble(goldSymbol,SYMBOL_POINT);
   if(point<=0 || (tick.ask-tick.bid)/point>maxSpreadPoints) return "SPREAD_LIMIT";
   double minVol=SymbolInfoDouble(goldSymbol,SYMBOL_VOLUME_MIN);
   double maxVol=SymbolInfoDouble(goldSymbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(goldSymbol,SYMBOL_VOLUME_STEP);
   if(configuredLot<minVol || configuredLot>maxVol || step<=0 || MathAbs(configuredLot/step-MathRound(configuredLot/step))>0.00001) return "VOLUME_RULE";
   if(OpenPlatformPositions()>=maxPositions) return "POSITION_LIMIT";
   if(TodayPlatformProfit()<=-dailyLossLimit) return "DAILY_LOSS_LIMIT";
   price=(side=="BUY"?tick.ask:tick.bid);
   if(sl<=0 || (side=="BUY" && sl>=price) || (side=="SELL" && sl<=price)) return "INVALID_STOP_LOSS";
   int stops=(int)SymbolInfoInteger(goldSymbol,SYMBOL_TRADE_STOPS_LEVEL);
   if(MathAbs(price-sl)<stops*point) return "BROKER_STOP_LEVEL";
   double margin;
   ENUM_ORDER_TYPE type=(side=="BUY"?ORDER_TYPE_BUY:ORDER_TYPE_SELL);
   if(!OrderCalcMargin(type,goldSymbol,configuredLot,price,margin) || margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)) return "INSUFFICIENT_MARGIN";
   double tickSize=SymbolInfoDouble(goldSymbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(goldSymbol,SYMBOL_TRADE_TICK_VALUE);
   double contract=SymbolInfoDouble(goldSymbol,SYMBOL_TRADE_CONTRACT_SIZE);
   if(tickSize<=0 || tickValue<=0 || contract<=0) return "INVALID_CONTRACT_SPEC";
   MqlTradeRequest checkRequest={};
   MqlTradeCheckResult checkResult={};
   checkRequest.action=TRADE_ACTION_DEAL;
   checkRequest.symbol=goldSymbol;
   checkRequest.volume=configuredLot;
   checkRequest.type=type;
   checkRequest.price=price;
   checkRequest.sl=sl;
   checkRequest.magic=PlatformMagic;
   int fills=(int)SymbolInfoInteger(goldSymbol,SYMBOL_FILLING_MODE);
   if((fills & SYMBOL_FILLING_IOC)!=0) checkRequest.type_filling=ORDER_FILLING_IOC;
   else if((fills & SYMBOL_FILLING_FOK)!=0) checkRequest.type_filling=ORDER_FILLING_FOK;
   else checkRequest.type_filling=ORDER_FILLING_RETURN;
   if(!OrderCheck(checkRequest,checkResult) || checkResult.retcode!=TRADE_RETCODE_DONE)
      return "ORDER_CHECK_FAILED";
   return "";
}

void Report(string id,string state,string reason,string requestId,ulong order,ulong deal,ulong position,double price,double volume,double sl,int retcode,string side)
{
   string body="{\"signal_id\":\""+Esc(id)+"\",\"state\":\""+state+"\",\"reason\":\""+Esc(reason)+
      "\",\"request_id\":\""+Esc(requestId)+"\",\"broker_order\":"+IntegerToString((long)order)+
      ",\"broker_deal\":"+IntegerToString((long)deal)+",\"position_ticket\":"+IntegerToString((long)position)+
      ",\"retcode\":"+IntegerToString(retcode)+",\"symbol\":\""+Esc(goldSymbol)+"\",\"side\":\""+side+
      "\",\"volume\":"+DoubleToString(volume,4)+",\"price\":"+DoubleToString(price,8)+
      ",\"stop_loss\":"+DoubleToString(sl,8)+"}";
   string answer;
   Request("POST","/api/ea/report",body,answer);
}

void CheckClosures()
{
   string answer;
   if(!Request("GET","/api/ea/open-trades","",answer)) return;
   int cursor=StringFind(answer,"\"trades\":[");
   if(cursor<0) return;
   while(true)
   {
      int p=StringFind(answer,"{",cursor), q=StringFind(answer,"}",cursor);
      if(p<0 || q<0 || q<p) break;
      string item=StringSubstr(answer,p,q-p+1);
      string id=Field(item,"signal_id");
      ulong position=(ulong)StringToInteger(Field(item,"position_ticket"));
      cursor=q+1;
      if(id=="" || position==0) continue;
      bool stillOpen=false;
      for(int i=0;i<PositionsTotal();i++)
      {
         ulong ticket=PositionGetTicket(i);
         if(ticket>0 && PositionSelectByTicket(ticket) && (ulong)PositionGetInteger(POSITION_IDENTIFIER)==position)
            stillOpen=true;
      }
      if(stillOpen || !HistorySelectByPosition(position)) continue;
      double profit=0,closePrice=0;
      ulong closingDeal=0;
      for(int j=0;j<HistoryDealsTotal();j++)
      {
         ulong d=HistoryDealGetTicket(j);
         profit+=HistoryDealGetDouble(d,DEAL_PROFIT)+HistoryDealGetDouble(d,DEAL_COMMISSION)+HistoryDealGetDouble(d,DEAL_SWAP);
         if(HistoryDealGetInteger(d,DEAL_ENTRY)==DEAL_ENTRY_OUT)
         { closingDeal=d; closePrice=HistoryDealGetDouble(d,DEAL_PRICE); }
      }
      if(closingDeal==0 || closePrice<=0) continue;
      string body="{\"signal_id\":\""+Esc(id)+"\",\"state\":\"closed\",\"position_ticket\":"+
         IntegerToString((long)position)+",\"broker_deal\":"+IntegerToString((long)closingDeal)+
         ",\"price\":"+DoubleToString(closePrice,8)+",\"profit\":"+DoubleToString(profit,2)+"}";
      string ignored;
      Request("POST","/api/ea/report",body,ignored);
   }
}

void ProcessSignal(string object)
{
   string id=Field(object,"id"),side=Field(object,"side"),base=Field(object,"symbol_base");
   if(id=="" || Field(object,"provider")!="DEMO_ONLY" || base!="XAU" || (side!="BUY" && side!="SELL")) return;
   string guard="VT_"+id;
   if(GlobalVariableCheck(guard)) return;
   GlobalVariableSet(guard,(double)TimeCurrent()); // Conservative at-most-once attempt across restarts.
   double sl=StringToDouble(Field(object,"stop_loss"));
   string expiry=Field(object,"expires_at");
   StringReplace(expiry,"T"," "); StringReplace(expiry,"-",".");
   int plus=StringFind(expiry,"+"); if(plus>0) expiry=StringSubstr(expiry,0,plus);
   if(StringToTime(expiry)<=TimeGMT()) { Report(id,"rejected","STALE_SIGNAL","",0,0,0,0,0,sl,0,side); return; }
   double price=0;
   string reason=CheckSignal(side,sl,price);
   if(reason!="") { Report(id,"rejected",reason,"",0,0,0,0,0,sl,0,side); return; }
   string requestId=id;
   trade.SetExpertMagicNumber(PlatformMagic);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFillingBySymbol(goldSymbol);
   bool sent=(side=="BUY"?trade.Buy(configuredLot,goldSymbol,0,sl,0,StringSubstr(id,0,20)):
                          trade.Sell(configuredLot,goldSymbol,0,sl,0,StringSubstr(id,0,20)));
   uint rc=trade.ResultRetcode();
   ulong order=trade.ResultOrder(),deal=trade.ResultDeal();
   if(!sent || (rc!=TRADE_RETCODE_DONE && rc!=TRADE_RETCODE_DONE_PARTIAL) || deal==0)
   {
      Report(id,"rejected","BROKER_REJECTED_OR_UNCONFIRMED",requestId,order,deal,0,0,0,sl,(int)rc,side);
      return;
   }
   if(!HistoryDealSelect(deal)) { Report(id,"submitted","DEAL_HISTORY_PENDING",requestId,order,deal,0,0,0,sl,(int)rc,side); return; }
   ulong position=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
   double fill=HistoryDealGetDouble(deal,DEAL_PRICE);
   double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
   if(position==0 || fill<=0 || volume<=0) { Report(id,"submitted","FILL_UNCONFIRMED",requestId,order,deal,0,0,0,sl,(int)rc,side); return; }
   Report(id,"filled","",requestId,order,deal,position,fill,volume,sl,(int)rc,side);
}

int OnInit()
{
   if(!Demo()) { Print("LIVE TRADING BLOCKED BY CODE"); return INIT_FAILED; }
   if(StringFind(ServerBaseUrl,"https://")!=0) { Print("HTTPS required"); return INIT_FAILED; }
   LoadToken();
   if(eaToken=="" && !Link()) { Print("Enter a fresh link code from private bot chat"); return INIT_FAILED; }
   EventSetTimer(15);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer()
{
   if(!Demo()) return; // Hard stop even if the logged-in account changes.
   CheckClosures(); // Existing broker stops remain active even during coordinator outage.
   serverEnabled=false;
   if(!Heartbeat()) return; // Fail closed on network/auth/subscription failure.
   if(LocalPause || !serverEnabled) return;
   string answer;
   if(!Request("GET","/api/ea/signals","",answer)) return;
   int p=StringFind(answer,"\"signals\":[{");
   if(p<0) return;
   p=StringFind(answer,"{",p);
   int q=StringFind(answer,"}",p);
   if(p>=0 && q>p) ProcessSignal(StringSubstr(answer,p,q-p+1));
}
