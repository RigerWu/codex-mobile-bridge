package io.github.try2love.codexbridge;

/** Select only the cookie name confirmed by this gateway's auth response. */
final class BindingSelection {
 static boolean name(String value){return value!=null&&value.matches("codex_mobile_session_[a-f0-9]{24}|codex_mobile_session");}
 static String cookie(String header,String name){
  if(header!=null&&name(name))for(String part:header.split(";")){String value=part.trim();if(value.startsWith(name+"="))return value;}
  return "";
 }
 static String match(String sent,String current,String name){
  String selected=cookie(current,name),previous=cookie(sent,name);
  if(previous.isEmpty()&&!"codex_mobile_session".equals(name))previous=cookie(sent,"codex_mobile_session");
  if(selected.isEmpty()||previous.isEmpty()||!selected.substring(selected.indexOf('=')+1).equals(previous.substring(previous.indexOf('=')+1)))return "";
  return selected;
 }
}
